# SavVio — GCP Deployment Strategy

## Architecture Overview

The application is split into two compute tiers on GCP:

```
┌─────────────────── GCE VM (e2-standard-2) ─────────────────────┐
│  Docker Compose:                                                 │
│    - Airflow webserver + scheduler + worker  (port 8080)        │
│    - Redis (Celery broker)                   (port 6379)        │
│    - (connects to Cloud SQL — no local Postgres on VM)          │
└──────────────────────────────────────────────────────────────────┘

┌──────── Cloud Run (serverless, auto-scales to 0) ───────────────┐
│  savvio-{env}-api        FastAPI inference    port 8080          │
│  savvio-{env}-frontend   Nginx / React        port 8501          │
│  savvio-{env}-mlflow     MLflow tracking UI   port 5000          │
│  savvio-{env}-training   ML training job      (on-demand)        │
└─────────────────────────────────────────────────────────────────┘

┌──────── Managed GCP Services ───────────────────────────────────┐
│  Cloud SQL          PostgreSQL 15 + pgvector extension           │
│  Cloud Storage      dvc-data bucket, mlflow-artifacts bucket     │
│  Artifact Registry  Docker images for Cloud Run services         │
│  Secret Manager     DB password, API keys                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Why This Split?

| Component | Where | Reason |
|-----------|-------|--------|
| Airflow (data pipeline) | GCE VM | Needs persistent state, Redis, long-running scheduler — not suited to serverless |
| ML Training | Cloud Run (job) | On-demand containerized job — scales to 0 when idle, no SSH/VM overhead, Cloud Run jobs support up to 24 h timeout with `--task-timeout` |
| FastAPI inference | Cloud Run | Stateless, bursty traffic — auto-scales to 0 when idle, cost-efficient |
| React frontend | Cloud Run | Static nginx serving — serverless is ideal |
| MLflow UI | Cloud Run | Lightweight, stateless UI backed by GCS artifacts |
| PostgreSQL | Cloud SQL | Managed backups, pgvector support, shared by all services |

---

## CI/CD Flow

Each component has its own GitHub Actions workflow with automated triggers:

```
┌─ Code push to data_pipeline/** ──────────────────────────────────┐
│  datapipeline_ci.yml                                             │
│    1. Unit tests (mocked data — no real GCS calls)              │
│    2. DAG validation (python import check — catches syntax/dep   │
│       errors before they reach Airflow)                          │
│    3. DB connection check                                        │
│    4. [main only] SSH → VM → git pull → airflow dags reserialize │
└──────────────────────────────────────────────────────────────────┘

┌─ Code push to model_pipeline/** ─────────────────────────────────┐
│  modelpipeline_ci.yml                                            │
│    1. Unit tests                                                 │
│    2. Build training Docker image → push to Artifact Registry   │
│    3. Execute Cloud Run job (savvio-{env}-training)              │
│    4. Quality gates: F1 > 0.70, ROC-AUC > 0.75, bias < 0.10    │
│    5. [gates pass] Model artifacts → GCS                        │
│    6. [gates pass] Trigger deployment_ci.yml to redeploy API    │
└──────────────────────────────────────────────────────────────────┘

┌─ Code push to deployment/** or savviocore/** or model artifacts ─┐
│  deployment_ci.yml                                               │
│    1. API tests + Frontend lint/tests (parallel)                 │
│    2. Build Docker images → push to Artifact Registry (parallel) │
│    3. Deploy to Cloud Run (API + Frontend)                       │
│    4. Health check: GET /health must return 200                  │
│    5. Drift detection report (Evidently AI)                      │
└──────────────────────────────────────────────────────────────────┘

┌─ Code push to deployment/terraform/** ───────────────────────────┐
│  terraform.yml                                                   │
│    PR:   terraform plan  (shows what will change)                │
│    main: terraform apply (provisions/updates infrastructure)     │
└──────────────────────────────────────────────────────────────────┘
```

---

## What the Data Pipeline CI Does NOT Do

A common misconception: the CI for the data pipeline should **not** run the actual Airflow DAG.

The Airflow DAG runs in **production on the GCE VM** on a `@daily` schedule — it pulls real data from GCS/APIs and writes to Cloud SQL. This is expensive, slow, and side-effect-prone.

CI only validates:
- The DAG file parses without import errors
- Each module's logic is correct (unit tests with mocked/fixture data)
- The database is reachable

The separation is: **CI proves the code is correct → Airflow runs the code on schedule**.

---

## Terraform Infrastructure

Defined in `deployment/terraform/environments/{dev,prod}/main.tf`.

| Resource | Type | Purpose |
|----------|------|---------|
| `savvio-{env}-pipeline-vm` | `google_compute_instance` | GCE VM for Airflow |
| `savvio-{env}-training` | Cloud Run job | On-demand ML training |
| `savvio-{env}-db-instance` | Cloud SQL (PostgreSQL 15) | Shared database |
| `savvio-{env}-api` | Cloud Run service | FastAPI inference |
| `savvio-{env}-frontend` | Cloud Run service | React frontend |
| `savvio-{env}-mlflow` | Cloud Run service | MLflow tracking |
| `savvio-{env}-docker-repo` | Artifact Registry | Docker image storage |
| `savvio-{env}-dvc-data` | GCS bucket | DVC data cache |
| `savvio-{env}-mlflow-artifacts` | GCS bucket | MLflow model artifacts |
| `savvio-{env}-db-password` | Secret Manager | DB password |
| `savvio-{env}-run-sa` | Service Account | Identity for Cloud Run + VM |

> The GCE VM resource (`google_compute_instance`) needs to be added to `main.tf` — it is not yet defined in Terraform.

---

## GitHub Actions Secrets Required

| Secret | Used By | What it is |
|--------|---------|------------|
| `GCP_SA_KEY` | All workflows | GCP service account JSON key |
| `GCP_PROJECT_ID` | All workflows | GCP project ID |
| `GCE_VM_IP` | datapipeline | External IP of pipeline VM |
| `GCE_SSH_PRIVATE_KEY` | datapipeline | SSH private key for VM access |
| `DB_HOST` | datapipeline | Cloud SQL host |
| `DB_PORT` | datapipeline | 5432 |
| `DB_NAME` | datapipeline | Database name |
| `DB_USER` | datapipeline | Database user |
| `DB_PASSWORD` | datapipeline | Database password |
| `API_URL_DEV` | deployment | Cloud Run API URL (baked into frontend image at build time) |
| `SLACK_WEBHOOK_URL` | deployment | Optional — drift detection alerts |

---

## Known Issues to Fix in Existing Workflows

### `datapipeline_ci.yml`
- [ ] Add `push` / `pull_request` triggers — currently `workflow_dispatch` only (keeping manual per team decision)
- [x] Remove "Run data pipeline" step — runs real ingestion in CI (wrong)
- [x] Remove `cp -r dags/src/data/raw/` — source path does not exist
- [x] Add DAG parse validation step
- [x] Add pip caching
- [x] `data-quality-check` job: add GCP auth (currently missing), remove duplicate `tests/validation/`
- [x] Add `tests/database/` to unit-tests (currently never run by any job)
- [x] Add `deploy-dags` job (SSH to VM on main merge)

### `deployment_ci.yml`
- [ ] Uncomment the `push` trigger block (already written, just commented out — keeping manual per team decision)
- [x] `test-api`: add `pip install -e savviocore` (API imports from it)
- [x] `drift-detection`: add `needs: [deploy]` (currently runs in parallel with tests)
- [x] `deploy`: change `gcloud run services update` → `gcloud run deploy` (update fails on first deploy)
- [x] `deploy`: add `actions/checkout@v4` step (missing — gcloud needs the workspace)

### `modelpipeline_ci.yml`
- [x] Replace SSH-to-VM training step with Cloud Run job execution (`gcloud run jobs execute`)
- [x] Add step to build and push training Docker image to Artifact Registry
- [x] Add `--wait` flag to `gcloud run jobs execute` so CI blocks until training completes
- [x] Parse Cloud Run job logs for quality gate metrics (F1, ROC-AUC, bias)

### `deployment/terraform/environments/{dev,prod}/main.tf`
- [x] Add `google_compute_instance` resource for pipeline VM
- [x] Add `google_compute_firewall` resource for SSH access
- [x] Add VM IP to `outputs.tf`
- [x] Add `google_cloud_run_v2_job` resource for `savvio-{env}-training`

---

## First-Time Setup Checklist

1. **GCP project** — enable billing, create project
2. **Terraform** — run `terraform apply` in `deployment/terraform/environments/dev/` to provision all infrastructure (including the Cloud Run training job)
3. **VM setup** — SSH into the new GCE VM, clone the repo to `/opt/savvio`, set up `.env`, run `docker-compose up -d` for Airflow
4. **Cloud SQL** — run database migrations / initial schema via `savviocore`
5. **GitHub secrets** — add all secrets listed above to the repo
6. **SSH key** — generate a key pair, add public key to VM metadata, add private key as `GCE_SSH_PRIVATE_KEY` secret (only needed for Airflow/data pipeline)
7. **Training image** — build and push the training Docker image to Artifact Registry, create the Cloud Run job via `gcloud run jobs create`
8. **First deployment** — manually trigger `deployment_ci.yml` via `workflow_dispatch` to build and deploy the initial Cloud Run images
9. **Verify** — check `GET /health` on the Cloud Run API URL returns `{"status": "ok"}`
