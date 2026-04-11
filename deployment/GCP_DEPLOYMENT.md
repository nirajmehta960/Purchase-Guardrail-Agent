# SavVio — GCP Deployment Strategy

## Architecture Overview

The application is split into two compute tiers on GCP:

```
┌─────────────────── GCE VM (e2-standard-2) ─────────────────────┐
│  Docker Compose:                                                 │
│    - Airflow webserver + scheduler + worker  (port 8080)        │
│    - Redis (Celery broker)                   (port 6379)        │
│    - (connects to Cloud SQL — no local Postgres on VM)          │
│                                                                  │
│  ML Training (on-demand, triggered by CI/CD):                   │
│    - Runs on GitHub Actions runner (XGBoost, not deep learning) │
│    - Connects to Cloud SQL via DB secrets                        │
│    - Artifacts written to GCS mlflow bucket                     │
└──────────────────────────────────────────────────────────────────┘

┌──────── Cloud Run (serverless, auto-scales to 0) ───────────────┐
│  savvio-{env}-api        FastAPI inference    port 8080          │
│  savvio-{env}-frontend   Nginx / React        port 8501          │
│  savvio-{env}-mlflow     MLflow tracking UI   port 5000          │
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
| ML Training | GitHub Actions runner | XGBoost training is fast enough for a 6h runner limit; connects to Cloud SQL via secrets |
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
│    2. Run training on GitHub runner (connects to Cloud SQL)      │
│    3. Quality gates: F1 > 0.70, ROC-AUC > 0.75, bias < 0.10    │
│    4. Rollback check: F1 drop < 0.02 vs previous model          │
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
│    push to main: plan + apply in one step (dev environment)      │
│    NOTE: no PR plan-only check exists yet — apply runs directly  │
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
| `savvio-{env}-pipeline-vm` | `google_compute_instance` | GCE VM for Airflow + training |
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
| `GCE_VM_IP` | datapipeline | External IP of pipeline VM (for DAG deployment) |
| `GCE_SSH_PRIVATE_KEY` | datapipeline | SSH private key for VM access |
| `DB_HOST` | datapipeline, modelpipeline | Cloud SQL host |
| `DB_PORT` | datapipeline, modelpipeline | 5432 |
| `DB_NAME` | datapipeline, modelpipeline | Database name |
| `DB_USER` | datapipeline, modelpipeline | Database user |
| `DB_PASSWORD` | datapipeline, modelpipeline | Database password |
| `API_URL_DEV` | deployment | Cloud Run API URL (baked into frontend image at build time) |
| `SLACK_WEBHOOK_URL` | deployment | Optional — drift detection alerts |

---

## Known Issues to Fix in Existing Workflows

### `datapipeline_ci.yml`
- [ ] Add `push` / `pull_request` triggers — currently `workflow_dispatch` only (never runs automatically)
- [ ] Remove "Run data pipeline" step — runs real ingestion in CI (wrong)
- [ ] Remove `cp -r dags/src/data/raw/` — source path does not exist
- [ ] Add DAG parse validation step
- [ ] Add pip caching
- [ ] `data-quality-check` job: add GCP auth (currently missing), remove duplicate `tests/validation/`
- [ ] Add `tests/database/` to unit-tests (currently never run by any job)
- [ ] Add `deploy-dags` job (SSH to VM on main merge)

### `deployment_ci.yml`
- [ ] Uncomment the `push` trigger block (already written, just commented out)
- [ ] `test-api`: add `pip install -e savviocore` (API imports from it)
- [ ] `drift-detection`: add `needs: [deploy]` (currently runs in parallel with tests)
- [ ] `deploy`: change `gcloud run services update` → `gcloud run deploy` (update fails on first deploy)
- [ ] `deploy`: add `actions/checkout@v4` step (missing — gcloud needs the workspace)

### `modelpipeline_ci.yml`
- [ ] Add `push` / `pull_request` triggers — currently `workflow_dispatch` only
- [ ] **Critical**: `metrics.txt` and `bias_metrics.txt` are written in the `run-pipeline` job but read in `validation-gate` and `bias-gate` jobs — each job runs in a fresh environment so these files are lost between jobs. Fix: upload as artifacts after `run-pipeline` and download them in each gate job using `actions/upload-artifact` / `actions/download-artifact`
- [ ] **Critical**: `previous_metrics.txt` (needed by rollback-check) is never generated or stored anywhere — rollback check always skips. Fix: persist the last passing `metrics.txt` to GCS and download it as `previous_metrics.txt` at the start of each run
- [ ] DB password is not URL-encoded in the connection string (unlike datapipeline_ci.yml) — will fail if password contains special characters
- [ ] Add pip caching
- [ ] After all gates pass: trigger `deployment_ci.yml` via `workflow_dispatch` to redeploy the API with the new model

### `terraform.yml`
- [ ] Add `pull_request` trigger with `terraform plan` only — currently `terraform apply` runs directly on push to main with no PR preview step
- [ ] Replace hardcoded `sed -i 's/project_id = "savvio-ai"/.../` with `${{ secrets.GCP_PROJECT_ID }}` via `-var` flag — the sed approach is fragile and will break if the tfvars file changes

### `deployment/terraform/environments/dev/main.tf`
- [ ] Add `google_compute_instance` resource for pipeline VM
- [ ] Add `google_compute_firewall` resource for SSH access
- [ ] Add VM IP to `outputs.tf`
- [ ] Change API `min_instances` from `0` to `1` to eliminate cold starts on the inference API

---

## First-Time Deployment Order

> **Important:** The database currently only exists locally. Cloud SQL must be provisioned AND populated with data before CI/CD can run model training or the API can serve requests. Follow this order exactly.

### Step 1 — Provision Infrastructure
```bash
cd deployment/terraform/environments/dev
terraform init
terraform apply -var-file="terraform.tfvars"
```
This creates: Cloud SQL (empty), GCE VM, GCS buckets, Artifact Registry, Secret Manager entries.

### Step 2 — GitHub Secrets
Add all secrets listed in the table above to the repo (`Settings → Secrets and variables → Actions`).

### Step 3 — Set Up the GCE VM
```bash
# SSH into the VM
gcloud compute ssh savvio-dev-pipeline-vm --zone us-east1-b

# On the VM:
sudo git clone https://github.com/your-org/savvio /opt/savvio
cd /opt/savvio/data_pipeline
cp .env.example .env      # fill in Cloud SQL credentials from Terraform outputs
sudo docker-compose up -d
```

### Step 4 — Populate Cloud SQL (one-time)
The database schema is empty after Terraform. You must run the Airflow DAG once to load data:
```bash
# From the Airflow UI (port 8080 on the VM) or via CLI:
airflow dags trigger Data_pipeline_airflow
```
This runs the full pipeline: ingestion → preprocessing → feature engineering → DB loading.
Cloud SQL will now have `financial_profiles`, `products`, `reviews`, and vector embeddings.

### Step 5 — Run Model Training
Trigger `modelpipeline_ci.yml` manually via `workflow_dispatch` in GitHub Actions.
The runner connects to Cloud SQL, trains the XGBoost model, runs quality gates, and writes artifacts to GCS.

### Step 6 — Deploy the Application
Trigger `deployment_ci.yml` manually via `workflow_dispatch`.
This builds the Docker images, pushes to Artifact Registry, and deploys to Cloud Run.

### Step 7 — Verify
```bash
# Get the API URL from Terraform output or GCP Console
curl https://savvio-dev-api-xxx.run.app/health
# Expected: {"status": "ok", "model": "loaded", "db": "connected"}
```
