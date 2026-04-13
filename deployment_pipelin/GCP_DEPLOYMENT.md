# SavVio — GCP Deployment Guide

This document describes the production architecture and gives a step-by-step first-time deployment walkthrough that any team member can follow to reproduce the environment from scratch.

---

## Architecture Overview

```
┌─────────────────── GCE VM (e2-standard-4) ─────────────────────┐
│  Docker Compose stack (/opt/savvio/data_pipeline/):             │
│    - Airflow apiserver + scheduler + dag-processor + worker     │
│    - Airflow triggerer                                          │
│    - Redis (Celery broker)                  (internal)          │
│    - Postgres (Airflow metadata DB only)    (internal)          │
│    - Cloud SQL Auth Proxy (app data DB)     (internal :5432)    │
│  Airflow UI: http://<VM_IP>:8080                                │
│                                                                  │
│  Prometheus (Docker Compose, deployed by CI):                   │
│    - Scrapes FastAPI /metrics endpoint                          │
│    - Remote-writes to Grafana Cloud                             │
└──────────────────────────────────────────────────────────────────┘

┌──────── Cloud Run (serverless, auto-scales) ────────────────────┐
│  savvio-backend-api    FastAPI inference API    port 8080        │
│  savvio-ai             React / Nginx frontend   port 8080        │
│  savvio-ai-mlflow      MLflow tracking UI       port 5000        │
└─────────────────────────────────────────────────────────────────┘

┌──────── Managed GCP Services ───────────────────────────────────┐
│  Cloud SQL          PostgreSQL 15 (db-f1-micro) + pgvector      │
│  Cloud Storage      savvio-dev-dvc-data (DVC cache)             │
│                     savvio-dev-mlflow-artifacts (model artifacts)│
│  Artifact Registry  savvio-dev-docker-repo (Docker images)      │
│  Secret Manager     savvio-dev-db-password, grafana-api-key     │
└─────────────────────────────────────────────────────────────────┘
```

### Why this split?

| Component | Where | Reason |
|-----------|-------|--------|
| Airflow (data pipeline) | GCE VM | Needs persistent Redis + scheduler — not suited to serverless |
| ML Training | GitHub Actions runner | XGBoost is fast enough for CI runner; connects to Cloud SQL via proxy |
| FastAPI inference | Cloud Run | Stateless, bursty — auto-scales to 0 when idle |
| React frontend | Cloud Run | Static nginx — serverless ideal |
| MLflow UI | Cloud Run | Lightweight stateless UI backed by GCS artifacts |
| PostgreSQL | Cloud SQL | Managed backups, pgvector support, shared by all services |

---

## CI/CD Workflows

| Workflow | File | Trigger | What it does |
|----------|------|---------|--------------|
| Data Pipeline CI/CD | `datapipeline_ci.yml` | push/PR to `data_pipeline/**` | Unit tests, DAG parse validation, DB connection check. On main: SSH → VM → git pull → reserialize DAGs |
| Model Pipeline CI/CD | `modelpipeline_ci.yml` | push/PR to `model_pipeline/**` | Unit tests, DB check, run training, quality gates (F1>0.70, ROC-AUC>0.75), bias gate, rollback check, persist baseline to GCS, trigger deployment |
| Deployment CI/CD | `deployment.yml` | push to `deployment_pipeline/**` or `savviocore/**`, weekly cron, workflow_dispatch | API/frontend tests, build Docker images → Artifact Registry, deploy to Cloud Run, health check, drift detection, deploy Prometheus to VM |
| Terraform | `terraform.yml` | push/PR to `deployment_pipeline/terraform/**` | PR: plan only + posts plan as comment. Main push: plan + apply |

---

## Terraform-Managed Resources

All infrastructure is defined in `deployment_pipeline/terraform/environments/dev/main.tf`.

| Resource | GCP Name | Type |
|----------|----------|------|
| GCE pipeline VM | `savvio-dev-pipeline-vm` | `google_compute_instance` (e2-standard-4, us-east1-b) |
| Cloud SQL instance | `savvio-dev-db-instance` | PostgreSQL 15, db-f1-micro |
| Cloud SQL database | `savvio-dev-db` | — |
| Cloud Run API | `savvio-backend-api` | Cloud Run service |
| Cloud Run frontend | `savvio-ai` | Cloud Run service |
| Cloud Run MLflow | `savvio-ai-mlflow` | Cloud Run service |
| Artifact Registry | `savvio-dev-docker-repo` | Docker repository |
| DVC data bucket | `savvio-dev-dvc-data` | GCS bucket |
| MLflow artifact bucket | `savvio-dev-mlflow-artifacts` | GCS bucket |
| DB password secret | `savvio-dev-db-password` | Secret Manager |
| Grafana API key secret | `savvio-dev-grafana-api-key` | Secret Manager |
| Cloud Run service account | `savvio-dev-run-sa` | Service Account |
| Terraform state bucket | `savvio-purchase-guardrail-tf-state` | GCS bucket (created manually — not in Terraform) |

Terraform state is stored in GCS: `gs://savvio-purchase-guardrail-tf-state/env/dev`.

---

## GitHub Actions Secrets Required

Add all of these under **Settings → Secrets and variables → Actions** in the GitHub repo.

| Secret | Used by | What it is |
|--------|---------|------------|
| `GCP_SA_KEY` | All workflows | GCP service account JSON key (full content of the downloaded JSON file) |
| `GCP_PROJECT_ID` | All workflows | `savvio-purchase-guardrail` |
| `DB_INSTANCE_CONNECTION_NAME` | All workflows | `savvio-purchase-guardrail:us-east1:savvio-dev-db-instance` |
| `DB_HOST` | datapipeline, modelpipeline, deployment | `127.0.0.1` (CI uses Cloud SQL Proxy locally) |
| `DB_PORT` | datapipeline, modelpipeline, deployment | `5432` |
| `DB_NAME` | datapipeline, modelpipeline, deployment | `savvio-dev-db` |
| `DB_USER` | datapipeline, modelpipeline, deployment | `dev-db-admin` |
| `DB_PASSWORD` | datapipeline, modelpipeline, deployment | From Terraform output → Secret Manager (see Step 1) |
| `GCE_VM_IP` | datapipeline, deployment | External IP of pipeline VM — update after every VM restart |
| `GCE_SSH_PRIVATE_KEY` | datapipeline, deployment | ed25519 private key for `github-actions` user on VM |
| `API_URL_DEV` | deployment | `https://savvio-backend-api-ebw2ryzjkq-ue.a.run.app` (baked into frontend at build time) |
| `GRAFANA_REMOTE_WRITE_URL` | deployment | Grafana Cloud Prometheus remote-write URL |
| `GRAFANA_CLOUD_USERNAME` | deployment | Grafana Cloud instance numeric ID |
| `GRAFANA_CLOUD_API_KEY` | deployment | Grafana Cloud API key |
| `SLACK_WEBHOOK_URL` | deployment | Slack webhook for drift alerts (optional) |

> **Note on `GCE_VM_IP`**: The VM uses an ephemeral public IP. Every time the VM is restarted or recreated, the IP changes. After any VM restart you must get the new IP from `terraform output pipeline_vm_ip` and update this secret — otherwise the datapipeline and deployment CI/CD jobs that SSH into the VM will fail with `i/o timeout`.

---

## First-Time Deployment

Follow these steps in order. Steps 1–4 are manual (one-time setup). After that, CI/CD takes over for all future changes.

---

### Prerequisites

Before starting, install these tools locally:
- [gcloud CLI](https://cloud.google.com/sdk/docs/install)
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.14.7
- [Docker](https://docs.docker.com/get-docker/) (for local testing only)

Authenticate with GCP:
```bash
gcloud auth login
gcloud config set project savvio-purchase-guardrail
```

---

### Step 0 — One-time GCP bootstrap (run once, never again)

These resources must exist before Terraform can run. Create them manually:

**a) Create the Terraform state bucket:**
```bash
gcloud storage buckets create gs://savvio-purchase-guardrail-tf-state \
  --location=us-east1 \
  --uniform-bucket-level-access
```

**b) Create the service account that Terraform and CI/CD will use:**
```bash
gcloud iam service-accounts create savvio-cicd-sa \
  --display-name="SavVio CI/CD Service Account"

# Grant required roles
SA="savvio-cicd-sa@savvio-purchase-guardrail.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding savvio-purchase-guardrail \
  --member="serviceAccount:$SA" --role="roles/editor"
gcloud projects add-iam-policy-binding savvio-purchase-guardrail \
  --member="serviceAccount:$SA" --role="roles/iam.securityAdmin"
gcloud projects add-iam-policy-binding savvio-purchase-guardrail \
  --member="serviceAccount:$SA" --role="roles/secretmanager.admin"
gcloud projects add-iam-policy-binding savvio-purchase-guardrail \
  --member="serviceAccount:$SA" --role="roles/storage.admin"

# Grant access to the Terraform state bucket
gcloud storage buckets add-iam-policy-binding \
  gs://savvio-purchase-guardrail-tf-state \
  --member="serviceAccount:$SA" \
  --role="roles/storage.objectAdmin"
```

**c) Download the service account key** (this becomes the `GCP_SA_KEY` GitHub secret):
```bash
gcloud iam service-accounts keys create ~/savvio-sa-key.json \
  --iam-account="savvio-cicd-sa@savvio-purchase-guardrail.iam.gserviceaccount.com"
```

**d) Create the data bucket** (used by Airflow DAGs — referenced by name, not managed by Terraform):
```bash
gcloud storage buckets create gs://savvio-data-bucket \
  --location=us-east1 \
  --uniform-bucket-level-access
```

**e) Generate the SSH keypair for VM access:**

This keypair allows GitHub Actions to SSH into the GCE VM. The public key is already hardcoded in the VM startup script in `deployment_pipeline/terraform/environments/dev/main.tf`. If you need to rotate it:

1. Generate a new ed25519 keypair:
   ```bash
   ssh-keygen -t ed25519 -C "github-actions@savvio" -f ~/.ssh/savvio_github_actions -N ""
   ```
2. Replace the public key in `main.tf` → `metadata_startup_script` → the `authorized_keys` echo line.
3. Add the private key content as the `GCE_SSH_PRIVATE_KEY` GitHub secret.

---

### Step 1 — Provision infrastructure with Terraform

```bash
cd deployment_pipeline/terraform/environments/dev

# Authenticate Terraform using the service account key
export GOOGLE_APPLICATION_CREDENTIALS=~/savvio-sa-key.json

terraform init
terraform apply -var-file="terraform.tfvars"
```

Terraform will create all resources. After apply completes, capture the outputs:

```bash
terraform output
# Example output:
# api_url                = "https://savvio-backend-api-ebw2ryzjkq-ue.a.run.app"
# mlflow_url             = "https://savvio-ai-mlflow-ebw2ryzjkq-ue.a.run.app"
# db_connection_name     = "savvio-purchase-guardrail:us-east1:savvio-dev-db-instance"
# pipeline_vm_ip         = "35.x.x.x"
# docker_repo_url        = "us-east1-docker.pkg.dev/savvio-purchase-guardrail/savvio-dev-docker-repo"
```

Get the DB password from Secret Manager:
```bash
gcloud secrets versions access latest \
  --secret="savvio-dev-db-password"
```

> **Grafana variables**: Terraform variables `grafana_remote_write_url`, `grafana_cloud_username`, and `grafana_api_key` default to empty strings if not provided. The Grafana secret in Secret Manager will be created with value `"not-configured"` and can be updated later. To provide them at apply time, pass `-var="grafana_api_key=<key>"` or set `TF_VAR_grafana_api_key`.

---

### Step 2 — Add GitHub Actions secrets

Using the values from Step 1, add all secrets listed in the [GitHub Actions Secrets Required](#github-actions-secrets-required) table above.

Key mappings:
- `GCP_SA_KEY` → contents of `~/savvio-sa-key.json`
- `DB_PASSWORD` → output of `gcloud secrets versions access latest --secret="savvio-dev-db-password"`
- `DB_INSTANCE_CONNECTION_NAME` → `terraform output db_connection_name`
- `GCE_VM_IP` → `terraform output pipeline_vm_ip`
- `GCE_SSH_PRIVATE_KEY` → contents of `~/.ssh/savvio_github_actions` (private key)
- `API_URL_DEV` → `terraform output api_url`

---

### Step 3 — Set up Airflow on the GCE VM

The VM is created by Terraform with Docker pre-installed (via startup script). SSH in using the VM IP:

```bash
gcloud compute ssh savvio-dev-pipeline-vm \
  --zone=us-east1-b \
  --tunnel-through-iap
```

On the VM, clone the repo and configure the environment:

```bash
# Clone the repo
sudo git clone https://github.com/your-org/SavVio /opt/savvio
cd /opt/savvio/data_pipeline

# Create the .env file — copy the values from deployment_pipeline/.env or fill in manually
sudo cp /opt/savvio/deployment_pipeline/.env.example /opt/savvio/data_pipeline/.env
sudo nano /opt/savvio/data_pipeline/.env
```

The `.env` file must contain at minimum:
```
# Airflow metadata DB (local postgres container — keep these as-is)
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://airflow:airflow@postgres/airflow
AIRFLOW__CELERY__RESULT_BACKEND=db+postgresql://airflow:airflow@postgres/airflow

# Cloud SQL (application data DB)
DB_INSTANCE_CONNECTION_NAME=savvio-purchase-guardrail:us-east1:savvio-dev-db-instance
DB_NAME=savvio-dev-db
DB_USER=dev-db-admin
DB_PASSWORD=<from Secret Manager>
DB_HOST=cloud-sql-proxy
DB_PORT=5432

# GCS
GCS_DATA_BUCKET=savvio-data-bucket

# SMTP (optional — Airflow email alerts)
SMTP_USER=<gmail>
SMTP_PASSWORD=<app password>

# Slack (optional)
SLACK_WEBHOOK_URL=<url>
```

Build the custom Airflow image and start the stack:

```bash
cd /opt/savvio/data_pipeline
sudo AIRFLOW_UID=$(id -u) docker compose up --build -d
```

Wait for all services to become healthy (~2–3 minutes):
```bash
sudo docker compose ps
```

All services should show `healthy`. The Airflow UI is now at `http://<VM_IP>:8080` (login: `airflow` / `airflow`).

Unpause the DAG so it runs on schedule:
```bash
sudo docker compose exec airflow-apiserver \
  airflow dags unpause Data_pipeline_airflow
```

> **VM restart gotcha**: The VM startup script runs on first boot but not on restart. The `github-actions` SSH key is provisioned by the startup script, so if the VM is stopped/started (not rebooted), the key will still be in `authorized_keys` since the home directory persists. However, if the VM is **deleted and recreated** (e.g., by `terraform apply`), you must redo this step — the repo clone and `.env` will be gone. Update `GCE_VM_IP` in GitHub secrets with the new IP.

---

### Step 4 — Populate Cloud SQL (one-time data load)

Cloud SQL is empty after Terraform. Trigger the Airflow DAG once to run the full ingestion pipeline:

From the Airflow UI at `http://<VM_IP>:8080`:
1. Go to **DAGs** → `Data_pipeline_airflow`
2. Click **Trigger DAG** (play button)

Or from the VM command line:
```bash
sudo docker compose exec airflow-apiserver \
  airflow dags trigger Data_pipeline_airflow
```

This runs: ingestion → preprocessing → validation → feature engineering → DB loading.
Cloud SQL will now have `financial_profiles`, `products`, `reviews`, and `product_features` tables populated.

Monitor progress in the Airflow UI. The full run takes ~10–20 minutes.

---

### Step 5 — Run model training

Trigger the model pipeline workflow via GitHub Actions:

1. Go to **Actions** → `Model Pipeline CI/CD`
2. Click **Run workflow** → select `main` branch
3. For the first run, set `skip_rollback_check` = `true` (no baseline exists yet)

The workflow will:
- Run unit tests + DB connection check
- Train the XGBoost model against Cloud SQL data
- Run quality gates (F1 > 0.70, ROC-AUC > 0.75, bias checks)
- Write model artifacts to `gs://savvio-dev-mlflow-artifacts/`
- Save `metrics.txt` to GCS as the rollback baseline
- Automatically trigger the Deployment CI/CD workflow on success

---

### Step 6 — Deploy the application

If Step 5 succeeded, deployment is triggered automatically. You can also trigger it manually:

1. Go to **Actions** → `Deployment CI/CD`
2. Click **Run workflow** → select `main` branch

The workflow will:
- Run API tests and frontend lint/tests
- Build Docker images → push to Artifact Registry
- Deploy to Cloud Run: `savvio-backend-api`, `savvio-ai`, `savvio-ai-mlflow`
- Run health check against `/health`
- Run Evidently AI drift detection
- Deploy Prometheus to the GCE VM (SSHes in and runs docker compose)

---

### Step 7 — Verify

```bash
# Health check — should return {"status":"ok","model":"loaded","db":"connected"}
curl https://savvio-backend-api-ebw2ryzjkq-ue.a.run.app/health

# Frontend — should load the React app
open https://savvio-ai-ebw2ryzjkq-ue.a.run.app

# MLflow UI
open https://savvio-ai-mlflow-ebw2ryzjkq-ue.a.run.app

# Airflow UI
open http://<VM_IP>:8080

# Grafana monitoring dashboard
open https://nirajmehta2410.grafana.net/d/savvio-monitoring-v1/savvio-e28094-api-and-inference-monitoring
```

---

## Ongoing Operations

### Updating the Airflow DAGs

Any push to `main` that changes `data_pipeline/**` triggers the datapipeline CI/CD workflow, which:
1. Validates the DAG parses correctly
2. Runs unit tests
3. SSHes into the VM, does `git pull`, and rerializes DAGs

No manual VM access needed for DAG changes.

### Retraining the model

Push changes to `model_pipeline/**` or manually trigger `modelpipeline_ci.yml`. Gates must pass before deployment is triggered.

### Infrastructure changes

Edit files in `deployment_pipeline/terraform/environments/dev/`. Push to a PR to see a plan comment. Merge to main to apply.

### VM IP changed after restart

```bash
# Get new IP
gcloud compute instances describe savvio-dev-pipeline-vm \
  --zone=us-east1-b \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'

# Update the GitHub secret
gh secret set GCE_VM_IP --body "<new-ip>"
```

### Cloud SQL connection exhaustion

The `db-f1-micro` tier allows ~25 connections. The MLflow Cloud Run service is configured with `--workers 1` and `pool_size=2&max_overflow=0` to stay well under this limit. If you see `FATAL: remaining connection slots are reserved`, check how many Cloud Run revisions are active:

```bash
gcloud run revisions list --service=savvio-ai-mlflow --region=us-east1
```

Delete old revisions or reduce `min_instances` to 0 to free connections.

---

## Terraform Gotchas

- **Stale state lock**: If a Terraform run is interrupted, it leaves a lock in GCS. The CI workflow auto-unlocks it. Locally: `terraform force-unlock -force <LOCK_ID>`. Find the lock ID with `gcloud storage cat gs://savvio-purchase-guardrail-tf-state/env/dev/default.tflock`.

- **Cloud Run `client`/`client_version` drift**: `gcloud run deploy` sets metadata fields that Terraform would otherwise detect as drift and trigger new revisions. These are in `lifecycle { ignore_changes = [...] }` so `terraform plan` stays clean.

- **Placeholder images in tfvars**: `terraform.tfvars` sets placeholder images (`gcr.io/cloudrun/placeholder`). Terraform ignores the `image` field in `lifecycle.ignore_changes`, so real images deployed by CI are never overwritten by Terraform.

- **Cloud Run service names are hardcoded**: The three Cloud Run services use fixed names (`savvio-backend-api`, `savvio-ai`, `savvio-ai-mlflow`) regardless of environment. This is intentional — the names were locked in when the services were first deployed and their URLs distributed to other systems.
