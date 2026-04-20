# SavVio — Reproducing the GCP Deployment

This guide lets a teammate fork the repo and bring up the **entire stack** in
**their own GCP project** without editing a single workflow or pipeline file.

Everything project-specific is expressed as **GitHub repository
variables/secrets**, **GCP Secret Manager entries**, or
**Terraform `*.tfvars`**. If you ever find yourself editing
`.github/workflows/*.yml` or `data_pipeline/scripts/setup_secrets.sh` just to
change a name or a region, that's a bug — open a PR promoting the value to a
variable.

For a tour of what each workflow actually does, see
[`.github/workflows/README.md`](../.github/workflows/README.md).

---

## 0. Prerequisites

Install locally:

| Tool | Purpose |
|---|---|
| `gcloud` CLI (authenticated) | Provision GCP resources, populate Secret Manager |
| `terraform` ≥ 1.5 | Bring up VM / Cloud SQL / Artifact Registry / GCS / Cloud Run shells |
| `gh` CLI *(optional)* | Set GitHub repo vars/secrets from the terminal |
| `ssh` | Diagnose the VM if CI fails |
| `docker` | Optional — local build sanity checks |

Assumptions: you have **Owner** (or equivalent) on the target GCP project and
**admin** on the GitHub fork. You also want a **Grafana Cloud** account if you
intend to run `ops-monitoring.yml` — the free tier is enough.

---

## 1. Create the GCP project

```bash
export GCP_PROJECT_ID="<your-project-id>"
gcloud projects create "$GCP_PROJECT_ID" --name="SavVio Reproduction"
gcloud config set project "$GCP_PROJECT_ID"

# Link billing (brand-new project only).
gcloud billing projects link "$GCP_PROJECT_ID" --billing-account=<BILLING_ACCOUNT_ID>

# Enable every API the stack touches.
gcloud services enable \
  compute.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  aiplatform.googleapis.com
```

Create a GCS bucket for the Terraform **remote state** (versioning ON):

```bash
gcloud storage buckets create "gs://${GCP_PROJECT_ID}-tf-state" \
  --location="$AR_REGION" --uniform-bucket-level-access
gcloud storage buckets update "gs://${GCP_PROJECT_ID}-tf-state" \
  --versioning
```

Then edit `deployment/terraform/environments/{dev,prod}/backend.tf` and point
`bucket = "..."` at that name. This is **the one place** a project-specific
value lives in code, because Terraform backend blocks can't read variables.

---

## 2. Provision infrastructure with Terraform

```bash
cd deployment/terraform/environments/dev
# Edit terraform.tfvars — project_id, region, zone, image placeholders,
# Cloud Run service names, data_bucket_name, ci_ssh_public_keys.
terraform init
terraform apply
```

The modules in `deployment/terraform/modules/` provision:

- Artifact Registry repo (Docker format)
- Airflow VM (GCE instance) + instance service account
- Cloud SQL (Postgres) + Cloud SQL Proxy config
- GCS bucket for raw / processed / features / validated / MLflow artifacts
- Cloud Run service **shells** for API / Frontend / MLflow (images are a
  placeholder `gcr.io/...:latest` — real images are pushed by
  `deployment.yml` and `modelpipeline.yml`)
- Cloud Run **Job** shell for the trainer (`CLOUD_RUN_ML_TRAINER`)
- Cloud Run runtime service account (`RUN_SA_EMAIL`) with `run.invoker` and
  Cloud SQL client bindings
- Secret Manager resources (empty versions — filled in step 3)

### 2.1 Terraform variables worth calling out

Set these in `terraform.tfvars` (or pass via `TF_VAR_*`):

| Variable | Why |
|---|---|
| `project_id`, `region`, `zone`, `environment` | Targets the GCP project / region. |
| `name_prefix` | Prepended to every resource name and used as the `project` label. Default `savvio`. |
| `api_service_name`, `frontend_service_name`, `mlflow_service_name` | **Must equal** `vars.CLOUD_RUN_API` / `CLOUD_RUN_FRONTEND` / `CLOUD_RUN_MLFLOW` in GitHub Actions, or `gcloud run deploy` in CI silently targets a non-existent service. Set to `null` to fall back to `<prefix>-api` / `-frontend` / `-mlflow`. |
| `data_bucket_name` | Externally-managed GCS bucket the Cloud Run SA gets `objectAdmin` on. Empty string skips the IAM grant. |
| `ci_ssh_public_keys` | List of SSH public keys installed on the pipeline VM for `ci_ssh_user` (default `github-actions`). **Must include the public half of `GCE_SSH_PRIVATE_KEY`.** |
| `ssh_source_ranges`, `airflow_ui_source_ranges` | Firewall CIDRs. Default `["0.0.0.0/0"]` — **tighten for prod**. |
| `pipeline_vm_machine_type` / `_disk_image` / `_disk_gb`, `vpc_network` | VM tuning. |
| `api_*` / `frontend_*` / `mlflow_*` (`min_instances`, `max_instances`, `cpu`, `memory`) | Cloud Run sizing per service. |
| `grafana_remote_write_url`, `grafana_cloud_username`, `grafana_api_key` | Pass via `TF_VAR_*` (mirrors the CI secrets) — **never** put `grafana_api_key` in `terraform.tfvars`. |

Capture the outputs you'll need later:

```bash
terraform output -raw pipeline_vm_ip
terraform output -raw pipeline_vm_service_account_email
terraform output -raw ar_repo_name
terraform output -raw gcs_bucket_name
terraform output -raw db_connection_name
terraform output -raw api_url
terraform output -raw mlflow_url
```

> **Lock recovery.** If a Terraform run is interrupted mid-apply, the GCS state
> lock may linger. From the environment dir: `terraform force-unlock <ID>`.

---

## 3. Populate Secret Manager

A helper script prompts once for each secret value.

```bash
# From the repo root
bash data_pipeline/scripts/setup_secrets.sh "$GCP_PROJECT_ID"
# Or with a custom prefix:
bash data_pipeline/scripts/setup_secrets.sh "$GCP_PROJECT_ID" myproj
```

It upserts these 12 secrets (default prefix `savvio`):

| Secret | Used for |
|---|---|
| `${PREFIX}-db-user` / `-db-password` / `-db-host` / `-db-port` / `-db-name` | Cloud SQL connection — read by the VM at deploy time |
| `${PREFIX}-smtp-host` / `-smtp-port` / `-smtp-user` / `-smtp-password` | Airflow alert emails |
| `${PREFIX}-alert-email-from` / `-alert-email-list` | Alert email routing |
| `${PREFIX}-airflow-www-password` | Airflow UI admin password |

The prefix is configurable — see §5, `SECRET_PREFIX`.

---

## 4. Grant IAM to the runtime service accounts

### 4.1 VM instance service account

The Airflow VM authenticates to GCP via its **instance service account** (no
key files on disk). It needs:

```bash
VM_SA="$(terraform -chdir=deployment/terraform/environments/dev \
          output -raw pipeline_vm_service_account_email)"

for ROLE in \
    roles/secretmanager.secretAccessor \
    roles/artifactregistry.reader \
    roles/storage.objectAdmin \
    roles/cloudsql.client; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:${VM_SA}" --role="$ROLE"
done
```

*(If the Terraform modules already add some of these, the binding is a no-op —
run anyway to be safe.)*

### 4.2 Cloud Run runtime service account

The Cloud Run services (API, Frontend, MLflow, trainer Job) run as
`RUN_SA_EMAIL`. It needs `cloudsql.client`, `storage.objectAdmin` on the data
bucket, and `secretmanager.secretAccessor` on the secrets it reads at start-up.
The Terraform modules handle this; verify with:

```bash
gcloud projects get-iam-policy "$GCP_PROJECT_ID" \
  --flatten="bindings[].members" \
  --filter="bindings.members:${RUN_SA_EMAIL}" \
  --format="table(bindings.role)"
```

### 4.3 CI service account

The CI SA (whose JSON key lives in `GCP_SA_KEY`) needs:

- `roles/artifactregistry.writer` — push images
- `roles/run.admin` — deploy to Cloud Run
- `roles/iam.serviceAccountUser` on `RUN_SA_EMAIL` — required to attach the
  runtime SA to the Cloud Run service
- `roles/storage.objectAdmin` on `gs://${MLFLOW_GCS_BUCKET}` — `modelpipeline.yml`
  reads and writes `ci/previous_metrics.txt`
- `roles/cloudsql.client` — `modelpipeline.yml` and `ops-monitoring.yml` both
  run the Cloud SQL Auth Proxy on the runner
- `roles/compute.viewer` — `ops-monitoring.yml` describes the API Cloud Run
  service to resolve the scrape target

---

## 5. Configure GitHub

Go to **Repo → Settings → Secrets and variables → Actions**.

### 5a. Secrets (encrypted; used by CI only)

| Secret | Required | Description |
|---|---|---|
| `GCP_PROJECT_ID` | ✅ | GCP project ID (referenced as a secret by `modelpipeline`, `deployment`, `ops-monitoring`; as a variable by `datapipeline` — set it as both, same value). |
| `GCP_SA_KEY` | ✅ | JSON key for the CI SA from §4.3. |
| `GCE_VM_IP` | ✅ | Public IP of the Airflow / monitoring VM (from `terraform output pipeline_vm_ip`). |
| `GCE_SSH_PRIVATE_KEY` | ✅ | Private key whose public half is authorised on the VM user (`SSH_USER`). |
| `DB_INSTANCE_CONNECTION_NAME` | ✅ (model pipeline, ops-monitoring) | `<proj>:<region>:<instance>` — used by the Cloud SQL Auth Proxy. |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | ✅ (model pipeline, ops-monitoring) | Application DB credentials used while training / running drift detection in CI. |
| `API_URL_DEV` | ✅ (deployment) | Public URL of the deployed API. Baked into the frontend image at build time as `VITE_API_BASE`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | optional* | SMTP relay credentials (Gmail app password works). |
| `ALERT_EMAIL_FROM`, `ALERT_EMAIL_LIST` | optional* | Sender / recipients for CI emails. |
| `GRAFANA_CLOUD_API_KEY`, `GRAFANA_CLOUD_USERNAME`, `GRAFANA_REMOTE_WRITE_URL` | ✅ (terraform, ops-monitoring) | Grafana Cloud remote-write creds. `terraform.yml` exposes them as `TF_VAR_*`; `ops-monitoring.yml` substitutes them into `prometheus.production.yml`. |

<sub>* These duplicate the Secret Manager values because CI jobs run on the
GitHub runner, which has no GCE metadata-server access. The VM reads its copy
from Secret Manager at deploy time.</sub>

### 5b. Variables (plaintext; discoverable in the UI)

**Required — no defaults:**

| Variable | Example | Used by |
|---|---|---|
| `GCP_PROJECT_ID` | `my-gcp-project-id` | data pipeline |
| `PROJECT_NAME` | `SavVio` | email subjects, Prometheus label fallback |
| `ENVIRONMENT` | `dev` | deploy logs / email subjects |
| `AR_REGION` | `us-east1` | all image / Cloud Run jobs |
| `AR_REPO` | `savvio-dev-docker-repo` | all build jobs |
| `AR_IMAGE_DATAPIPELINE` | `savvio-data-pipeline` | data pipeline |
| `AR_IMAGE_MODELPIPELINE` | `savvio-model-pipeline` | model pipeline |
| `AR_IMAGE_API` | `savvio-api` | deployment |
| `AR_IMAGE_FRONTEND` | `savvio-frontend` | deployment |
| `AR_IMAGE_MLFLOW` | `savvio-mlflow` | deployment |
| `CLOUD_RUN_API` | `savvio-backend-api` | deployment, ops-monitoring |
| `CLOUD_RUN_FRONTEND` | `savvio-ai` | deployment |
| `CLOUD_RUN_MLFLOW` | `savvio-ai-mlflow` | deployment, model pipeline (tracking URI) |
| `CLOUD_RUN_ML_TRAINER` | `savvio-ai-ml-trainer` | model pipeline |
| `RUN_SA_EMAIL` | `savvio-dev-run-sa@<proj>.iam.gserviceaccount.com` | model pipeline |
| `SSH_USER` | `github-actions` | data pipeline, ops-monitoring |
| `VM_DEPLOY_PATH` | `/opt/savvio/data_pipeline` | data pipeline |
| `AIRFLOW_DAG_ID` | `Data_pipeline_airflow` | data pipeline |
| `MLFLOW_GCS_BUCKET` | `savvio-dev-mlflow-artifacts` | model pipeline (baseline), ops-monitoring (drift baseline) |
| `F1_THRESHOLD` | `0.70` | model pipeline validation gate |
| `ROC_AUC_THRESHOLD` | `0.75` | model pipeline validation gate |
| `ROLLBACK_THRESHOLD` | `0.02` | model pipeline rollback check |
| `NOTIFY_EMAILS_ENABLED` | `true` / `false` | all notifications |

**Optional — each has a default:**

| Variable | Default | Why change it |
|---|---|---|
| `SECRET_PREFIX` | `savvio` | You passed a non-default prefix to `setup_secrets.sh`. |
| `GCS_BUCKET_NAME` | `savvio-data-bucket` | Your data bucket has a different name. |
| `VERTEX_LOCATION` | `us-east1` | Vertex AI is in a different region. |
| `GCP_CREDENTIALS_PATH` | `/opt/airflow/config/savvio-gcp-key.json` | The SA key file lives at a different path inside the Airflow container. |
| `MONITORING_VM_PATH` | `/home/${SSH_USER}/savvio-monitoring` | Monitoring stack lives in a non-default path on the VM. |
| `PROMETHEUS_PROJECT_LABEL` | `${PROJECT_NAME}` | You want a different external `project` label on scraped metrics. |
| `PROMETHEUS_API_JOB` | `${PROJECT_NAME}-api` | Different Prometheus `job_name` / `service` label for the API. |
| `TERRAFORM_VERSION` | `1.14.7` | Pin a different Terraform CLI version. |
| `VITE_DEFAULT_USER_ID` | _(empty)_ | Default user ID baked into the frontend at build time. |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_LIST` | _(empty)_ | Forwarded into the VM `.env` written by the data pipeline deploy job. Set if you want the Airflow container to see them as env vars (passwords stay in Secret Manager). |

### 5c. Fast setup via `gh` CLI

```bash
# === variables ===
gh variable set GCP_PROJECT_ID          -b "$GCP_PROJECT_ID"
gh variable set PROJECT_NAME            -b "SavVio"
gh variable set ENVIRONMENT             -b "dev"
gh variable set AR_REGION               -b "us-east1"
gh variable set AR_REPO                 -b "$(terraform -chdir=deployment/terraform/environments/dev output -raw ar_repo_name)"
gh variable set AR_IMAGE_DATAPIPELINE   -b "savvio-data-pipeline"
gh variable set AR_IMAGE_MODELPIPELINE  -b "savvio-model-pipeline"
gh variable set AR_IMAGE_API            -b "savvio-api"
gh variable set AR_IMAGE_FRONTEND       -b "savvio-frontend"
gh variable set AR_IMAGE_MLFLOW         -b "savvio-mlflow"
gh variable set CLOUD_RUN_API           -b "savvio-backend-api"
gh variable set CLOUD_RUN_FRONTEND      -b "savvio-ai"
gh variable set CLOUD_RUN_MLFLOW        -b "savvio-ai-mlflow"
gh variable set CLOUD_RUN_ML_TRAINER    -b "savvio-ai-ml-trainer"
gh variable set RUN_SA_EMAIL            -b "savvio-dev-run-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
gh variable set SSH_USER                -b "github-actions"
gh variable set VM_DEPLOY_PATH          -b "/opt/savvio/data_pipeline"
gh variable set AIRFLOW_DAG_ID          -b "Data_pipeline_airflow"
gh variable set MLFLOW_GCS_BUCKET       -b "${GCP_PROJECT_ID}-mlflow-artifacts"
gh variable set F1_THRESHOLD            -b "0.70"
gh variable set ROC_AUC_THRESHOLD       -b "0.75"
gh variable set ROLLBACK_THRESHOLD      -b "0.02"
gh variable set NOTIFY_EMAILS_ENABLED   -b "false"

# === secrets (read from files / env so nothing hits shell history) ===
gh secret set GCP_PROJECT_ID            -b "$GCP_PROJECT_ID"
gh secret set GCP_SA_KEY                < path/to/ci_sa_key.json
gh secret set GCE_VM_IP                 -b "$(terraform -chdir=deployment/terraform/environments/dev output -raw pipeline_vm_ip)"
gh secret set GCE_SSH_PRIVATE_KEY       < path/to/github-actions.key
gh secret set DB_INSTANCE_CONNECTION_NAME -b "$(terraform -chdir=deployment/terraform/environments/dev output -raw db_connection_name)"
# DB_* + SMTP_* + ALERT_* + API_URL_DEV + GRAFANA_* — set interactively:
gh secret set DB_HOST
gh secret set DB_PASSWORD
# ...etc.
```

---

## 6. One-time VM bootstrap

The VM hosts only the runtime; **no application source code** lives on it.
Perform once per VM, as `root` (or with `sudo`):

```bash
# Docker + Compose v2 (Debian/Ubuntu)
curl -fsSL https://get.docker.com | sh
sudo apt-get install -y docker-compose-plugin

# CI user with Docker access
sudo useradd -m -s /bin/bash github-actions
sudo usermod -aG docker github-actions
sudo install -d -o github-actions -g github-actions /opt/savvio/data_pipeline

# SSH key for the github-actions user (paste the public half that pairs with
# the private key stored in GitHub as GCE_SSH_PRIVATE_KEY).
sudo -u github-actions mkdir -p /home/github-actions/.ssh
sudo -u github-actions tee /home/github-actions/.ssh/authorized_keys < your_pubkey.pub
sudo chmod 600 /home/github-actions/.ssh/authorized_keys

# Non-interactive docker for the github-actions user.
echo 'github-actions ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose' \
  | sudo tee /etc/sudoers.d/github-actions

# Monitoring stack lives alongside, under the SSH_USER's home by default.
sudo -u github-actions mkdir -p /home/github-actions/savvio-monitoring/prometheus
```

The `.env` file on the VM is **rewritten on every data-pipeline deploy** from
Secret Manager, so you do not create it by hand. The bootstrap only needs the
directory structure and SSH access in place.

> **Why no `vm_env_setup.sh` in the repo?**
> That file historically held the production DB password and was committed by
> accident. `.env` is now produced at deploy time and listed in `.gitignore`.
> After this change the password should also be rotated in Cloud SQL since the
> old one is in git history.

---

## 7. Ship it

Push to `main`. In order:

| Order | Workflow | Why first |
|---|---|---|
| 1 | `terraform.yml` | Creates the AR repo, VM, Cloud SQL, GCS bucket, Cloud Run shells. Everything else assumes they exist. |
| 2 | `datapipeline.yml` | Builds the Airflow image and boots the VM runtime. Populates the raw / processed tables in Cloud SQL. |
| 3 | `deployment.yml` | Ships API + Frontend + MLflow images to Cloud Run. The MLflow URL is needed by the model pipeline. |
| 4 | `modelpipeline.yml` | Trains a model, persists the baseline, ships the trainer image. **First run — manually dispatch with `skip_rollback_check=true`** (no baseline exists yet). |
| 5 | `ops-monitoring.yml` (push variant) | Pushing changes under `deployment/monitoring/**` ships the Prometheus/Grafana stack to the VM. Weekly cron takes over afterwards. |

Typical iteration from this point is just `git push origin main` and letting
path filters pick the right workflow(s).

---

## 8. Verifying success

```bash
# 1. Which workflows ran recently?
gh run list --limit 20

# 2. API is healthy?
curl -sf "$(gcloud run services describe "${CLOUD_RUN_API}" \
             --region "${AR_REGION}" --format='value(status.url)')/health"

# 3. Frontend loads?
open "$(gcloud run services describe "${CLOUD_RUN_FRONTEND}" \
         --region "${AR_REGION}" --format='value(status.url)')"

# 4. MLflow UI?
open "$(gcloud run services describe "${CLOUD_RUN_MLFLOW}" \
         --region "${AR_REGION}" --format='value(status.url)')"

# 5. DAG is registered on the VM?
gcloud compute ssh <VM_NAME> --zone=<ZONE> --project="$GCP_PROJECT_ID" \
  --tunnel-through-iap --command="
    cd $VM_DEPLOY_PATH && \
    sudo -u github-actions docker compose \
      -f docker-compose.yaml -f docker-compose.prod.yaml \
      --env-file .env --env-file .env.ci \
      exec -T airflow-scheduler airflow dags list"

# 6. Airflow UI (login: airflow / <${SECRET_PREFIX}-airflow-www-password>)
#    http://<VM_PUBLIC_IP>:8080

# 7. Trainer Cloud Run Job exists?
gcloud run jobs describe "${CLOUD_RUN_ML_TRAINER}" --region "${AR_REGION}"

# 8. Latest drift report (after the weekly cron or a manual dispatch)
gh run list --workflow=ops-monitoring.yml --limit 1
gh run download <RUN_ID> -n drift-report-<RUN_ID>
```

---

## 9. What to change when forking

The **only** files that should need editing for a fork are:

1. `deployment/terraform/environments/{dev,prod}/terraform.tfvars` — project,
   region, resource names.
2. `deployment/terraform/environments/{dev,prod}/backend.tf` — the state
   bucket name.
3. Your **GitHub repo variables** (§5b) — mirror the Terraform outputs.

Everything else is driven by secrets, variables, or Terraform outputs.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ERROR: (gcloud.secrets.versions.access) PERMISSION_DENIED` during data-pipeline deploy | VM SA missing `secretmanager.secretAccessor` | §4.1 |
| `Error response from daemon: ... Unauthenticated request` pulling the image on the VM | VM SA missing `artifactregistry.reader` | §4.1 |
| `.env` exists on VM but `DB_PASSWORD=` is empty | A secret is missing in Secret Manager; `inherit_errexit` now fails fast | Re-run `setup_secrets.sh` and supply the missing value |
| Airflow UI: "No DAGs found" after a green CI run | DAG files aren't in the image | Ensure `COPY data_pipeline/dags /opt/airflow/dags` in `data_pipeline/Dockerfile` |
| `No data found` from `airflow dags list` | Import errors in the DAG | `airflow dags list-import-errors` on the VM |
| `modelpipeline.yml` fails on first run with "`previous_metrics.txt` not found" | No baseline in GCS yet | Dispatch manually with `skip_rollback_check=true`; the run persists the baseline for next time |
| `modelpipeline.yml` deploy fails with `service account ... does not exist` | `RUN_SA_EMAIL` var doesn't match what Terraform created | Copy it from `terraform output` (or `gcloud iam service-accounts list`) |
| `deployment.yml` fails with `service ... was not found` | `CLOUD_RUN_*` vars don't match the Terraform-created service names | Either update `terraform.tfvars` (`*_service_name`) or the GitHub vars so they agree |
| `ops-monitoring.yml` `deploy-monitoring` fails resolving the API host | `CLOUD_RUN_API` points at a service Terraform hasn't created yet | Run `deployment.yml` first, or verify the name |
| `terraform apply` hangs with "acquiring state lock" | Lock from a previous interrupted run | `cd deployment/terraform/environments/dev && terraform force-unlock <ID>` |
| Drift detector emails never arrive but CI passes | `ALERT_EMAIL_LIST` / `SMTP_*` missing or `NOTIFY_EMAILS_ENABLED=false` | Set them in §5a and flip the toggle to `true` |

---

## Appendix: Secret / variable cheat sheet

```text
REQUIRED GITHUB SECRETS               REQUIRED GITHUB VARS              GCP SECRET MANAGER
  GCP_PROJECT_ID                        GCP_PROJECT_ID                    ${PREFIX}-db-user
  GCP_SA_KEY                            PROJECT_NAME                      ${PREFIX}-db-password
  GCE_VM_IP                             ENVIRONMENT                       ${PREFIX}-db-host
  GCE_SSH_PRIVATE_KEY                   AR_REGION                         ${PREFIX}-db-port
  DB_INSTANCE_CONNECTION_NAME           AR_REPO                           ${PREFIX}-db-name
  DB_HOST  DB_PORT  DB_NAME             AR_IMAGE_DATAPIPELINE             ${PREFIX}-smtp-host
  DB_USER  DB_PASSWORD                  AR_IMAGE_MODELPIPELINE            ${PREFIX}-smtp-port
  API_URL_DEV                           AR_IMAGE_API                      ${PREFIX}-smtp-user
                                        AR_IMAGE_FRONTEND                 ${PREFIX}-smtp-password
OPTIONAL (notifications)                AR_IMAGE_MLFLOW                   ${PREFIX}-alert-email-from
  SMTP_HOST  SMTP_PORT                  CLOUD_RUN_API                     ${PREFIX}-alert-email-list
  SMTP_USER  SMTP_PASSWORD              CLOUD_RUN_FRONTEND                ${PREFIX}-airflow-www-password
  ALERT_EMAIL_FROM                      CLOUD_RUN_MLFLOW
  ALERT_EMAIL_LIST                      CLOUD_RUN_ML_TRAINER            REQUIRED VM SA IAM
                                        RUN_SA_EMAIL                      roles/secretmanager.secretAccessor
REQUIRED (monitoring)                   SSH_USER                          roles/artifactregistry.reader
  GRAFANA_CLOUD_API_KEY                 VM_DEPLOY_PATH                    roles/storage.objectAdmin
  GRAFANA_CLOUD_USERNAME                AIRFLOW_DAG_ID                    roles/cloudsql.client
  GRAFANA_REMOTE_WRITE_URL              MLFLOW_GCS_BUCKET
                                        F1_THRESHOLD                    REQUIRED CI SA IAM
                                        ROC_AUC_THRESHOLD                 roles/artifactregistry.writer
                                        ROLLBACK_THRESHOLD                roles/run.admin
                                        NOTIFY_EMAILS_ENABLED             roles/iam.serviceAccountUser
                                                                            (on RUN_SA_EMAIL)
                                      OPTIONAL VARS (have defaults)       roles/storage.objectAdmin
                                        SECRET_PREFIX                       (on MLFLOW_GCS_BUCKET)
                                        GCS_BUCKET_NAME                   roles/cloudsql.client
                                        VERTEX_LOCATION                   roles/compute.viewer
                                        GCP_CREDENTIALS_PATH
                                        MONITORING_VM_PATH
                                        PROMETHEUS_PROJECT_LABEL
                                        PROMETHEUS_API_JOB
                                        TERRAFORM_VERSION
                                        VITE_DEFAULT_USER_ID
```
