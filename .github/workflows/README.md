# GitHub Actions Workflows

This directory holds every CI/CD pipeline that ships SavVio. The workflows are
intentionally **project-agnostic** — every project-specific value comes from
GitHub **Secrets** (credentials) or **Variables** (non-secret config).

If you're trying to bring the stack up in a fresh GCP project, follow
[`deployment/REPRODUCE.md`](../../deployment/REPRODUCE.md) instead; this file is
a reference for what's already wired.

---

## Overview

| # | Workflow | File | Triggers | What it ships |
|---|---|---|---|---|
| 1 | **Terraform Infrastructure** | `terraform.yml` | push / PR on `deployment/terraform/**`, manual | GCP infra (Artifact Registry, Cloud SQL, VM, GCS, Cloud Run shells) |
| 2 | **Data Pipeline CI/CD** | `datapipeline.yml` | push / PR on `data_pipeline/**` or `savviocore/**`, manual | Airflow image → GCE VM (`docker compose up -d`) + DAG trigger |
| 3 | **Model Pipeline CI/CD** | `modelpipeline.yml` | push / PR on `model_pipeline/**` or `savviocore/**`, manual | Trainer image → Cloud Run **Job** (gated on F1 / ROC / bias / rollback) |
| 4 | **Deployment CI/CD** | `deployment.yml` | push / PR on `deployment/**` or `savviocore/**`, manual | API + Frontend + MLflow → Cloud Run **services** (tests only on PR, build+deploy on push/manual) |
| 5 | **Ops Monitoring & Drift Detection** | `ops-monitoring.yml` | weekly cron (Mon 08:00 UTC), push / PR on `deployment/monitoring/**`, manual | Prometheus/Grafana stack on the VM + drift alerts; auto-dispatches `modelpipeline.yml` on RED drift |

### Dependency graph

```
terraform.yml ─┐ (creates AR repo, VM, Cloud SQL, Cloud Run shells)
               │
               ├─► datapipeline.yml  ──► VM (Airflow)
               ├─► modelpipeline.yml ──► Cloud Run Job (trainer)
               ├─► deployment.yml    ──► Cloud Run Services (API / FE / MLflow)
               └─► ops-monitoring.yml
                     └─► (on RED drift) dispatches modelpipeline.yml
```

Everything after `terraform.yml` just pushes images and updates an existing
service — no infra mutation.

---

## 1. `terraform.yml` — Terraform Infrastructure

Provisions and maintains every GCP resource. Holds a **single global lock** via
`concurrency: terraform-state` so PR plans and main applies can't race over the
GCS state bucket.

**Jobs**

| Job | When | What |
|---|---|---|
| `terraform-dev` | always | `init` → `validate` → `plan` on every PR/push; `apply -auto-approve` on push to `main` only |

PR runs post the plan as a comment (truncated at 60 000 chars). Main pushes print
the key outputs at the end (`db_connection_name`, `api_url`, `mlflow_url`,
`pipeline_vm_ip`).

**Inputs it consumes**

- Secrets: `GCP_SA_KEY`, `GCP_PROJECT_ID`, `GRAFANA_CLOUD_API_KEY`, `GRAFANA_CLOUD_USERNAME`, `GRAFANA_REMOTE_WRITE_URL`
- Variables: `TERRAFORM_VERSION` (optional, default `1.14.7`)
- Files: `deployment/terraform/environments/dev/terraform.tfvars`, `backend.tf`

**Recovery note.** If a run is interrupted mid-apply, the GCS state lock may
linger — `cd deployment/terraform/environments/dev && terraform force-unlock <ID>`.

---

## 2. `datapipeline.yml` — Data Pipeline CI/CD

Builds the Airflow image, ships it to Artifact Registry, then SSHes into the
pipeline VM and swaps the running container in place.

**Jobs**

| Job | Needs | Gate | What |
|---|---|---|---|
| `unit-tests` | — | — | Installs `apache-airflow>=3.0` + deps, imports the DAG module (parse check), runs `pytest` across `tests/{bias,database,features,ingestion,preprocess,validation}` |
| `build-and-push` | `unit-tests` | `push` / `workflow_dispatch` only | `docker buildx` → AR; tags `:sha` + `:latest`; registry-side `buildcache` |
| `deploy` | `build-and-push` | `push` / `workflow_dispatch` only | SSH to VM → regenerates `.env` from Secret Manager → `scp` docker-compose files → `docker compose pull && up -d` → waits for scheduler + Celery worker → triggers DAG |
| `notify-on-failure` / `notify-on-success` | all three | — | SMTP email if `NOTIFY_EMAILS_ENABLED=true` |

**Deploy details worth knowing**

- The VM never checks out source. It only pulls the image the build job just
  produced and mounts `config/<sa-key>.json` read-only.
- `.env` is rewritten **on every deploy** from Secret Manager (prefix
  `${SECRET_PREFIX}`, default `savvio-dev`). `inherit_errexit` makes a missing
  secret hard-fail instead of producing an empty env var.
- Readiness is **three-stage**: scheduler responds (20×15s), DAG is parsed
  (12×10s), Celery worker registers with the broker (40×15s). The worker wait
  is critical — sentence-transformers import takes ~60–90s on cold start, long
  after the DAG shows up in `airflow dags list`.
- The DAG is triggered with run-id `deploy__${SHA}__${RUN_ID}` so it's
  traceable back to the exact workflow run.

---

## 3. `modelpipeline.yml` — Model Pipeline CI/CD

Trains the model inside the runner (not on Vertex), writes a metrics artifact,
gates on quality / fairness / regression, then ships a trainer image to Cloud
Run Jobs for scheduled re-runs.

**Jobs**

| Job | Needs | Gate | What |
|---|---|---|---|
| `unit-tests` | — | — | `pytest` across data / deterministic engine / features / bias-detection tests |
| `run-pipeline` | `unit-tests` | `push` / `workflow_dispatch` only | Starts Cloud SQL Auth Proxy (cached), resolves `MLFLOW_TRACKING_URI` from Cloud Run, runs `src/run_pipeline.py`; uploads `metrics.txt` + `bias_metrics.txt` + `previous_metrics.txt` as artifacts |
| `validation-gate` | `run-pipeline` | fails if `f1_score < F1_THRESHOLD` or `roc_auc < ROC_AUC_THRESHOLD` | Reads `metrics.txt` |
| `bias-gate` | `run-pipeline` | fails unless `bias_gate_passed=1` in `bias_metrics.txt` | `workflow_dispatch` input `skip_bias_gate=true` bypasses |
| `rollback-check` | both gates | fails if `previous_f1 - current_f1 > ROLLBACK_THRESHOLD` | No-op on first run. Input `skip_rollback_check=true` bypasses |
| `persist-metrics-baseline` | `rollback-check` | `push` / `workflow_dispatch` only | Copies current `metrics.txt` to `gs://${MLFLOW_GCS_BUCKET}/ci/previous_metrics.txt` — this is the next run's baseline |
| `build-and-push` | `persist-metrics-baseline` | `push` / `workflow_dispatch` only | Trainer image → AR |
| `deploy` | `build-and-push` | `push` / `workflow_dispatch` only | `gcloud run jobs deploy ${CLOUD_RUN_ML_TRAINER}` with `RUN_SA_EMAIL` |
| `notify-on-failure` / `notify-on-success` | all | — | SMTP |

**Manual overrides** (`workflow_dispatch` inputs)

- `skip_rollback_check=true` — use on first run when the GCS baseline doesn't exist yet.
- `skip_bias_gate=true` — debugging only; never merge with this on.

---

## 4. `deployment.yml` — API / Frontend / MLflow to Cloud Run

Ships the three user-facing services. Assumes the Cloud Run service shells
already exist (Terraform creates them).

**Jobs**

| Job | Needs | Gate | What |
|---|---|---|---|
| `test-api` | — | — | `pytest deployment/tests/` with `PYTHONPATH` covering `model_pipeline/src` + `savviocore/src` |
| `test-frontend` | — | — | Bun install (frozen lockfile) → `lint` → `test` |
| `build-push-api` | `test-api` | `push` / `workflow_dispatch` only | API image → AR |
| `build-push-mlflow` | `test-api` | `push` / `workflow_dispatch` only | MLflow image → AR |
| `build-push-frontend` | `test-frontend` | `push` / `workflow_dispatch` only | Frontend image → AR; passes `VITE_API_BASE=${API_URL_DEV}` and `VITE_DEFAULT_USER_ID` as **build args** |
| `deploy` | all three build jobs | `push` / `workflow_dispatch` only | `gcloud run deploy` for each of `CLOUD_RUN_API`, `CLOUD_RUN_FRONTEND`, `CLOUD_RUN_MLFLOW`; curl-based `/health` check on the API |
| `notify-on-failure` / `notify-on-success` | all | — | SMTP |

**PR behaviour.** On a pull-request targeting `main`, only `test-api` and
`test-frontend` run — no images are built or pushed and nothing is deployed.
Build/deploy jobs all carry `if: github.event_name != 'pull_request'`.

The frontend bakes `VITE_API_BASE` at build time, so changing the API URL
requires a new frontend build. (That's why `API_URL_DEV` is a secret —
updating it re-runs this workflow.)

---

## 5. `ops-monitoring.yml` — Drift Detection + Monitoring Stack

Has two largely-independent responsibilities, both running on every
`workflow_dispatch` or `schedule` trigger:

**Jobs**

| Job | `push` event | `pull_request` | `workflow_dispatch` | `schedule` | Notes |
|---|---|---|---|---|---|
| `drift-detection` | skipped | runs | runs | runs (Mon 08:00 UTC) | Cloud SQL Auth Proxy → Postgres (`127.0.0.1:5432`), pulls baseline from `gs://${MLFLOW_GCS_BUCKET}/monitoring/baseline_data.csv`, runs Evidently, uploads `drift_summary_*.json` as artifact, writes `severity` output (`NONE`/`YELLOW`/`RED`) |
| `trigger-retraining` | — | if severity=RED | if severity=RED | if severity=RED | Dispatches `modelpipeline.yml` against `main` via `actions/github-script` |
| `deploy-monitoring` | runs | skipped | runs | — | Renders `prometheus.production.yml` with `envsubst` (API host, Grafana remote-write), `scp`s it + `docker-compose.production.yml` to `MONITORING_VM_PATH`, `docker compose pull && up -d` |
| `notify-on-failure` | always | always | always | always | Runs if any job failed; sends SMTP if `NOTIFY_EMAILS_ENABLED=true` |
| `notify-on-success` | always | always | always | always | Runs if all jobs passed; sends SMTP if `NOTIFY_EMAILS_ENABLED=true` |

**Important:** The drift detector connects to the database via the Cloud SQL
Auth Proxy started in the previous step, using `DB_HOST=127.0.0.1`. Do **not**
change `DB_HOST` to the Cloud SQL public IP — direct TCP connections from GitHub
Actions runners are blocked by Cloud SQL.

**Prometheus config is rendered, not static.** `deployment/monitoring/prometheus/prometheus.production.yml`
is a template with `${API_HOST}`, `${PROMETHEUS_PROJECT_LABEL}`,
`${PROMETHEUS_API_JOB}`, `${GRAFANA_REMOTE_WRITE_URL}`,
`${GRAFANA_CLOUD_USERNAME}`, `${GRAFANA_CLOUD_API_KEY}` — substituted by
`envsubst` in the runner; the rendered file is what lands on the VM.

---

## Shared conventions

Every workflow follows the same handful of rules so they compose cleanly:

- **Concurrency.** `datapipeline`, `modelpipeline`, `deployment` all use
  `group: ${{ github.workflow }}-${{ github.ref }}` with
  `cancel-in-progress: true`. `ops-monitoring` runs with
  `cancel-in-progress: false` (don't kill a mid-flight drift run).
  `terraform.yml` uses a single global group `terraform-state`.
- **Auth.** Every job that touches GCP uses `google-github-actions/auth@v2`
  with `secrets.GCP_SA_KEY`. The VM authenticates separately via its **instance
  service account** (no key files on disk).
- **Image tagging.** Every build tags both `:${{ github.sha }}` (immutable,
  what deploy jobs pin to) and `:latest`, with registry-side `buildcache`.
- **PR safety.** Build and deploy jobs across all workflows carry
  `if: github.event_name != 'pull_request'` (or equivalent step-level guards).
  PRs run tests only — nothing is pushed to Artifact Registry or deployed to
  Cloud Run / the VM.
- **Email notifications.** All workflows gate SMTP on
  `vars.NOTIFY_EMAILS_ENABLED == 'true'` at the **step level** (not job level),
  so the notify jobs always appear in the run graph and you can flip emails off
  without touching secrets.
- **Path filters.** Workflows only trigger on changes under their own
  directory tree, so an API-only PR doesn't re-run the data pipeline and vice
  versa.

---

## Inputs reference

**Secrets (sensitive — never logged):**

```
GCP_PROJECT_ID              GCP_SA_KEY
GCE_VM_IP                   GCE_SSH_PRIVATE_KEY
DB_INSTANCE_CONNECTION_NAME
DB_PASSWORD
SMTP_PASSWORD
API_URL_DEV
GRAFANA_CLOUD_API_KEY       GRAFANA_CLOUD_USERNAME       GRAFANA_REMOTE_WRITE_URL
```

**Variables (visible in logs — safe for names, regions, toggles):**

```
# Identity / registry
PROJECT_NAME  ENVIRONMENT  AR_REGION  AR_REPO

# Image names
AR_IMAGE_DATAPIPELINE  AR_IMAGE_MODELPIPELINE
AR_IMAGE_API  AR_IMAGE_FRONTEND  AR_IMAGE_MLFLOW

# Cloud Run targets
CLOUD_RUN_API  CLOUD_RUN_FRONTEND  CLOUD_RUN_MLFLOW
CLOUD_RUN_ML_TRAINER  RUN_SA_EMAIL

# VM deploy
SSH_USER  VM_DEPLOY_PATH  AIRFLOW_DAG_ID
MONITORING_VM_PATH                 # optional, defaults to /home/${SSH_USER}/savvio-monitoring
PROMETHEUS_PROJECT_LABEL           # optional, defaults to PROJECT_NAME
PROMETHEUS_API_JOB                 # optional, defaults to ${PROJECT_NAME}-api

# Database (non-secret — used in drift detection via Cloud SQL proxy)
DB_HOST  DB_PORT  DB_NAME  DB_USER

# Storage / MLflow
MLFLOW_GCS_BUCKET  GCS_BUCKET_NAME  GCP_CREDENTIALS_PATH
SECRET_PREFIX  VERTEX_LOCATION

# Email / SMTP (non-secret — credentials are in secrets above)
SMTP_HOST  SMTP_PORT  SMTP_USER
ALERT_EMAIL_FROM  ALERT_EMAIL_LIST  ALERT_EMAIL_CC

# Quality gates
F1_THRESHOLD  ROC_AUC_THRESHOLD  ROLLBACK_THRESHOLD

# Toggles
NOTIFY_EMAILS_ENABLED  TERRAFORM_VERSION  VITE_DEFAULT_USER_ID
```

---

## Adding a new workflow

Checklist so new workflows compose with the rest:

1. Put it under `.github/workflows/` with a descriptive `name:`.
2. Scope triggers to `paths:` the workflow actually cares about; add both
   `push` and `pull_request` on `branches: [main]` plus `workflow_dispatch`.
3. Guard every build / deploy / destructive job with
   `if: github.event_name != 'pull_request'` so PRs only run tests.
4. Pick a `concurrency` group — use `${{ github.workflow }}-${{ github.ref }}`
   unless you have a reason not to.
5. Never hardcode project IDs, regions, bucket names, service names, or email
   addresses — add a new `vars.*` entry and document it in the inputs reference
   above.
6. Add `notify-on-failure` / `notify-on-success` jobs with
   `if: vars.NOTIFY_EMAILS_ENABLED == 'true'` at the **step level** (not job
   level) so the jobs always appear in the run graph.
7. Update this README's overview table and inputs reference.
