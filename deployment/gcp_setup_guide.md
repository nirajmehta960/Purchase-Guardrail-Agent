# SavVio — GCP Setup Guide (Step-by-Step)

> **Prerequisites**: GCP account, `gcloud` CLI installed, GitHub repo access, local Postgres with SavVio data.
> **Project ID**: `savvio-purchase-guardrail` · **Region**: `us-east1` · **Zone**: `us-east1-b`

---

## Phase 0 — One-Time Bootstrap

> These resources can't be managed by Terraform because Terraform needs them to exist first.

### Step 0.1 — Authenticate with GCP

```bash
gcloud auth login
gcloud config set project savvio-purchase-guardrail
```

### Step 0.2 — Enable Required APIs

```bash
gcloud services enable \
  compute.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com
```

> [!NOTE]
> Terraform also enables these APIs, but enabling them now prevents bootstrap failures when creating the SA and bucket.

### Step 0.3 — Create the Terraform State Bucket

```bash
gcloud storage buckets create gs://savvio-purchase-guardrail-tf-state \
  --project=savvio-purchase-guardrail \
  --location=us-east1 \
  --uniform-bucket-level-access
```

**Verify:**
```bash
gcloud storage buckets describe gs://savvio-purchase-guardrail-tf-state --format="value(name)"
# Expected: savvio-purchase-guardrail-tf-state
```

### Step 0.4 — Create the GitHub Actions Service Account

```bash
gcloud iam service-accounts create savvio-github-actions \
  --project=savvio-purchase-guardrail \
  --display-name="GitHub Actions CI/CD"
```

### Step 0.5 — Grant IAM Roles to the Service Account

```bash
SA="savvio-github-actions@savvio-purchase-guardrail.iam.gserviceaccount.com"

for ROLE in \
  roles/run.admin \
  roles/cloudsql.admin \
  roles/storage.admin \
  roles/artifactregistry.admin \
  roles/secretmanager.admin \
  roles/iam.serviceAccountUser \
  roles/compute.instanceAdmin.v1 \
  roles/iam.serviceAccountAdmin \
  roles/resourcemanager.projectIamAdmin; do
  gcloud projects add-iam-policy-binding savvio-purchase-guardrail \
    --member="serviceAccount:$SA" --role="$ROLE" --quiet
done
```

### Step 0.6 — Grant SA Access to the TF State Bucket

```bash
gcloud storage buckets add-iam-policy-binding \
  gs://savvio-purchase-guardrail-tf-state \
  --member="serviceAccount:$SA" --role="roles/storage.admin"
```

### Step 0.7 — Download the SA Key

```bash
gcloud iam service-accounts keys create gcp-sa-key.json \
  --iam-account="$SA"
```

> [!CAUTION]
> This file is a long-lived credential. **Never commit it to Git.** You'll paste its contents into GitHub Secrets in Phase 2. After that, consider deleting the local copy or storing it in a password manager.

**Verify:**
```bash
cat gcp-sa-key.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Client email: {d[\"client_email\"]}')"
# Expected: Client email: savvio-github-actions@savvio-purchase-guardrail.iam.gserviceaccount.com
```

### ✅ Phase 0 Checkpoint

- [ ] `gs://savvio-purchase-guardrail-tf-state` bucket exists
- [ ] `savvio-github-actions` SA exists with all 9 roles
- [ ] `gcp-sa-key.json` downloaded locally

---

## Phase 1 — Provision Infrastructure with Terraform

### Step 1.1 — Initialize Terraform

```bash
cd /Users/nirajmehta/Documents/SavVio/deployment/terraform/environments/dev

terraform init
```

**Expected output:** `Terraform has been successfully initialized!`

If you see a backend error, verify the state bucket exists (Step 0.3).

### Step 1.2 — Preview the Plan

```bash
terraform plan \
  -var="project_id=savvio-purchase-guardrail" \
  -var-file="terraform.tfvars"
```

**Expected:** ~15–20 resources to create (Cloud SQL, GCE VM, GCS buckets, Artifact Registry, Secret Manager, Cloud Run services, Service Account, Firewall rule).

Review the plan carefully. Key things to confirm:
- Cloud SQL instance: `savvio-dev-db-instance` (PostgreSQL 15, `db-f1-micro`)
- GCE VM: `savvio-dev-pipeline-vm` (`e2-standard-2`, Debian 12)
- GCS buckets: `savvio-dev-dvc-data`, `savvio-dev-mlflow-artifacts`
- Cloud Run: `savvio-dev-api`, `savvio-dev-frontend`, `savvio-dev-mlflow` (placeholder images — will be replaced by CI/CD)

### Step 1.3 — Apply

```bash
terraform apply \
  -var="project_id=savvio-purchase-guardrail" \
  -var-file="terraform.tfvars"
```

Type `yes` when prompted. This takes **5–10 minutes** (Cloud SQL is the slowest resource).

### Step 1.4 — Capture Outputs

```bash
echo "=== Save these values ==="
echo "DB Connection Name:  $(terraform output -raw db_connection_name)"
echo "Pipeline VM IP:      $(terraform output -raw pipeline_vm_ip)"
echo "Docker Repo URL:     $(terraform output -raw docker_repo_url)"
echo "API URL:             $(terraform output -raw api_url)"
echo "Frontend URL:        $(terraform output -raw frontend_url)"
echo "MLflow URL:          $(terraform output -raw mlflow_url)"
echo "DVC Bucket:          $(terraform output -raw dvc_bucket)"
echo "MLflow Bucket:       $(terraform output -raw mlflow_artifact_bucket)"
```

> [!IMPORTANT]
> Copy these values somewhere — you'll need them for GitHub Secrets and VM setup.

### Step 1.5 — Retrieve the DB Password

```bash
gcloud secrets versions access latest \
  --secret=savvio-dev-db-password \
  --project=savvio-purchase-guardrail
```

Copy this password. You'll need it in Phase 2 and Phase 3.

### ✅ Phase 1 Checkpoint

- [ ] `terraform apply` completed with no errors
- [ ] All outputs captured (DB connection name, VM IP, URLs, bucket names)
- [ ] DB password retrieved from Secret Manager

---

## Phase 2 — GitHub Secrets

Go to your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**.

Add each secret one by one:

| # | Secret Name | Value | Source |
|---|-------------|-------|--------|
| 1 | `GCP_SA_KEY` | Entire contents of `gcp-sa-key.json` | Phase 0, Step 0.7 |
| 2 | `GCP_PROJECT_ID` | `savvio-purchase-guardrail` | Hardcoded |
| 3 | `DB_HOST` | `127.0.0.1` | CI connects via Cloud SQL Auth Proxy |
| 4 | `DB_PORT` | `5432` | Standard Postgres port |
| 5 | `DB_NAME` | `savvio-dev-db` | Terraform creates this |
| 6 | `DB_USER` | `dev-db-admin` | Terraform creates this |
| 7 | `DB_PASSWORD` | *(from Step 1.5)* | Secret Manager |
| 8 | `DB_INSTANCE_CONNECTION_NAME` | *(from Step 1.4 — `db_connection_name`)* | e.g. `savvio-purchase-guardrail:us-east1:savvio-dev-db-instance` |
| 9 | `GCE_VM_IP` | *(from Step 1.4 — `pipeline_vm_ip`)* | Terraform output |
| 10 | `GCE_SSH_PRIVATE_KEY` | *(generated in Phase 3, Step 3.1)* | **Come back to this after Phase 3** |
| 11 | `API_URL_DEV` | *(from Step 1.4 — `api_url`)* | Terraform output |
| 12 | `SLACK_WEBHOOK_URL` | *(optional)* | Slack incoming webhook URL |

> [!NOTE]
> **`DB_HOST=127.0.0.1`** — This is correct. In CI, the Cloud SQL Auth Proxy runs as a background process and listens on `127.0.0.1:5432`. The workflows we've configured handle the proxy setup automatically.

> [!IMPORTANT]
> You'll need to come back and add `GCE_SSH_PRIVATE_KEY` after Phase 3 Step 3.1.

### ✅ Phase 2 Checkpoint

- [ ] All 11 secrets added (except `GCE_SSH_PRIVATE_KEY` — done after Phase 3)
- [ ] `GCP_SA_KEY` contains the full JSON (not just the key ID)

---

## Phase 3 — Set Up the GCE VM

### Step 3.1 — Generate SSH Key Pair

Run this **on your local machine** (not on the VM):

```bash
ssh-keygen -t ed25519 -f savvio-vm-key -C "github-actions" -N ""
```

This creates two files:
- `savvio-vm-key` — **Private key** → paste into GitHub secret `GCE_SSH_PRIVATE_KEY`
- `savvio-vm-key.pub` — **Public key** → add to the VM

**Now go back to GitHub and add the `GCE_SSH_PRIVATE_KEY` secret:**
```bash
cat savvio-vm-key
# Copy the ENTIRE output (including -----BEGIN/END-----) into the GitHub secret
```

### Step 3.2 — Add Public Key to VM Metadata

```bash
gcloud compute instances add-metadata savvio-dev-pipeline-vm \
  --zone=us-east1-b \
  --metadata="ssh-keys=github-actions:$(cat savvio-vm-key.pub)"
```

### Step 3.3 — SSH into the VM

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b
```

> [!NOTE]
> If this is your first SSH, `gcloud` will generate a key pair for your Google account. That's separate from the GitHub Actions key above.

### Step 3.4 — Set Up the VM (run these commands ON the VM)

```bash
# Verify Docker is running (installed by Terraform startup script)
sudo systemctl status docker
# If not active: sudo systemctl start docker && sudo systemctl enable docker

# If Docker isn't installed (rare — startup script may not have finished):
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo systemctl enable docker && sudo systemctl start docker

# Create the github-actions user
sudo useradd -m -s /bin/bash github-actions
sudo usermod -aG docker github-actions

# Clone the repo
sudo git clone https://github.com/YOUR_ORG/savvio /opt/savvio
sudo chown -R github-actions:github-actions /opt/savvio
```

### Step 3.5 — Create the `.env` File on the VM

```bash
# Get the DB password (run this on the VM — the VM's SA has Secret Manager access)
DB_PASS=$(gcloud secrets versions access latest \
  --secret=savvio-dev-db-password \
  --project=savvio-purchase-guardrail)

cat > /opt/savvio/data_pipeline/.env <<EOF
# ---- Application data (Cloud SQL via proxy sidecar) ----
DB_INSTANCE_CONNECTION_NAME=savvio-purchase-guardrail:us-east1:savvio-dev-db-instance
DB_HOST=cloud-sql-proxy
DB_PORT=5432
DB_NAME=savvio-dev-db
DB_USER=dev-db-admin
DB_PASSWORD=${DB_PASS}

# ---- Airflow settings ----
AIRFLOW_UID=50000
_AIRFLOW_WWW_USER_USERNAME=airflow
_AIRFLOW_WWW_USER_PASSWORD=airflow
EOF
```

> [!IMPORTANT]
> **`DB_HOST=cloud-sql-proxy`** — This points to the Docker service name of the Cloud SQL Auth Proxy sidecar in `docker-compose.yaml`. Do NOT use `127.0.0.1` here (that's only for CI). The Airflow metadata DB stays on the local `postgres` container — the `docker-compose.yaml` environment block overrides `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN` automatically.

### Step 3.6 — Start Airflow

> [!NOTE]
> The VM uses Docker Compose v1 (`docker-compose` with a hyphen), not v2 (`docker compose`). Always use the hyphen form on the VM.

```bash
cd /opt/savvio/data_pipeline
sudo docker-compose up -d
```

Wait ~60 seconds for initialization, then verify:

```bash
# Check all containers are running
sudo docker-compose ps

# Expected services: postgres, redis, cloud-sql-proxy, airflow-apiserver,
# airflow-scheduler, airflow-dag-processor, airflow-worker, airflow-triggerer, airflow-init (exited 0)

# Check Airflow is responsive
curl -s http://localhost:8080/api/v2/version
```

### Step 3.7 — Verify Cloud SQL Proxy Connectivity (on the VM)

```bash
# Exec into any airflow container and test the application DB connection
sudo docker-compose exec airflow-worker bash -c "
  pip install psycopg2-binary -q 2>/dev/null
  python3 -c \"
import psycopg2, os
conn = psycopg2.connect(
    host=os.environ.get('DB_HOST', 'cloud-sql-proxy'),
    port=5432,
    dbname='savvio-dev-db',
    user='dev-db-admin',
    password='${DB_PASS}'
)
cur = conn.cursor()
cur.execute('SELECT 1')
print(f'Cloud SQL connection OK: {cur.fetchone()}')
conn.close()
\"
"
```

### ✅ Phase 3 Checkpoint

- [ ] SSH key pair generated and public key added to VM
- [ ] `GCE_SSH_PRIVATE_KEY` secret added to GitHub
- [ ] `github-actions` user created on VM with Docker access
- [ ] Repo cloned to `/opt/savvio`
- [ ] `.env` created with correct `DB_HOST=cloud-sql-proxy`
- [ ] `docker compose up -d` succeeded, all containers healthy
- [ ] Cloud SQL proxy connectivity test passed

---

## Phase 4 — Populate Cloud SQL with Data

The Cloud SQL database is empty after Terraform provisioning. Choose one option:

### Option A — Re-run the Data Pipeline (Preferred)

If your raw data files are in GCS or accessible from the VM:

```bash
# From the Airflow UI at http://<VM_IP>:8080
# Login: airflow / airflow
# Find the DAG "Data_pipeline_airflow" → Toggle ON → Trigger

# OR via CLI on the VM:
cd /opt/savvio/data_pipeline
sudo docker-compose exec airflow-apiserver \
  airflow dags trigger Data_pipeline_airflow
```

This runs: **ingestion → preprocessing → feature engineering → DB loading**.

Monitor progress in the Airflow UI. Expected runtime: 10–30 minutes depending on data size.

### Option B — Dump & Restore from Local DB

If raw data isn't in GCS and you need to migrate your local Postgres:

**Step B.1** — Dump your local database:
```bash
pg_dump -h localhost -U postgres -d savvio \
  --no-owner --no-acl \
  -F c -f savvio_local.dump
```

**Step B.2** — Install Cloud SQL Auth Proxy locally (macOS Apple Silicon):
```bash
curl -o cloud-sql-proxy \
  https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.1/cloud-sql-proxy.darwin.arm64
chmod +x cloud-sql-proxy
```

**Step B.3** — Start the proxy (in a **separate terminal**):
```bash
./cloud-sql-proxy \
  savvio-purchase-guardrail:us-east1:savvio-dev-db-instance \
  --port 5433
```

> Port `5433` to avoid conflicting with your local Postgres on `5432`.

**Step B.4** — Enable pgvector extension:
```bash
psql -h 127.0.0.1 -p 5433 -U dev-db-admin -d savvio-dev-db \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Step B.5** — Restore:
```bash
pg_restore -h 127.0.0.1 -p 5433 \
  -U dev-db-admin -d savvio-dev-db \
  --no-owner --no-acl \
  savvio_local.dump
```

> [!NOTE]
> `--no-owner --no-acl` are **required** — Cloud SQL doesn't allow superuser operations.

**Step B.6** — Verify data loaded:
```bash
psql -h 127.0.0.1 -p 5433 -U dev-db-admin -d savvio-dev-db \
  -c "SELECT tablename FROM pg_tables WHERE schemaname='public';"
```

Expected tables: `financial_profiles`, `products`, `reviews`, and vector embedding tables.

### ✅ Phase 4 Checkpoint

- [ ] Cloud SQL has data (either via Airflow DAG or pg_restore)
- [ ] Key tables exist: `financial_profiles`, `products`, `reviews`

---

## Phase 5 — First Model Training

### Step 5.1 — Trigger Model Pipeline

Go to GitHub repo → **Actions** → **Model Pipeline CI/CD** → **Run workflow** (top right) → Click **Run workflow** on `main` branch.

### Step 5.2 — Monitor the Run

Watch the workflow execute these jobs in order:

```
unit-tests          → Should pass (~2 min)
db-connection-check → Should pass (~1 min, proxy connects to Cloud SQL)
run-pipeline        → Trains XGBoost model (~5-15 min)
validation-gate     → F1 > 0.70, ROC-AUC > 0.75
bias-gate           → Max disparity < 0.10
rollback-check      → Skips automatically (no previous baseline)
persist-metrics     → Saves metrics.txt to GCS as baseline
trigger-deployment  → Dispatches deployment_ci.yml
```

> [!NOTE]
> On the **first run**, `rollback-check` will always pass because there's no `previous_metrics.txt` yet. The `persist-metrics-baseline` job saves the current metrics as the baseline for future runs.

### Step 5.3 — If Gates Fail

If the validation or bias gates fail:
- Check the `metrics.txt` artifact in the workflow run
- The thresholds are: F1 ≥ 0.70, ROC-AUC ≥ 0.75, bias disparity < 0.10
- If close to threshold, you may need to tune hyperparameters or check data quality

### ✅ Phase 5 Checkpoint

- [ ] Model pipeline completed successfully
- [ ] All gates passed (validation, bias, rollback)
- [ ] Metrics baseline persisted to `gs://savvio-dev-mlflow-artifacts/ci/previous_metrics.txt`
- [ ] `deployment_ci.yml` was automatically triggered

---

## Phase 6 — First Application Deployment

### Step 6.1 — Monitor the Deployment

If `trigger-deployment` in Phase 5 dispatched `deployment_ci.yml`, it's already running. Otherwise, trigger manually:

GitHub → **Actions** → **Deployment CI/CD** → **Run workflow**.

The workflow runs:

```
test-api            → API unit tests (~2 min)
test-frontend       → Frontend lint + tests (~2 min)
    ↓ (parallel)
build-push-api      → Builds & pushes API Docker image
build-push-frontend → Builds & pushes Frontend Docker image
    ↓ (both done)
deploy              → Deploys to Cloud Run + health check
drift-detection     → Runs Evidently AI drift report
```

### Step 6.2 — Verify the Deployment

```bash
# Get the live API URL
API_URL=$(gcloud run services describe savvio-dev-api \
  --region=us-east1 --format='value(status.url)')
echo "API URL: $API_URL"

# Health check
curl -s "$API_URL/health" | python3 -m json.tool
```

**Expected response:**
```json
{
  "status": "ok",
  "model": "loaded",
  "db": "connected"
}
```

```bash
# Get the frontend URL
FRONTEND_URL=$(gcloud run services describe savvio-dev-frontend \
  --region=us-east1 --format='value(status.url)')
echo "Frontend URL: $FRONTEND_URL"

# Quick check
curl -s -o /dev/null -w "%{http_code}" "$FRONTEND_URL"
# Expected: 200
```

### Step 6.3 — Verify MLflow UI

```bash
MLFLOW_URL=$(gcloud run services describe savvio-dev-mlflow \
  --region=us-east1 --format='value(status.url)')
echo "MLflow URL: $MLFLOW_URL"
```

Open this URL in your browser. You should see the MLflow tracking UI.

### ✅ Phase 6 Checkpoint

- [ ] Deployment pipeline completed
- [ ] `/health` returns `{"status":"ok","model":"loaded","db":"connected"}`
- [ ] Frontend loads in browser
- [ ] MLflow UI is accessible

---

## Phase 7 — Post-Deployment Verification

### Step 7.1 — End-to-End Smoke Test

```bash
# Get the live API URL (if not already set)
API_URL=$(gcloud run services describe savvio-dev-api \
  --region=us-east1 --format='value(status.url)')

# Test natural language mode (user_id format in DB is U00001, U00002, ...)
curl -s -X POST "$API_URL/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "U00001",
    "user_query": "Can I afford to buy a refrigerator water filter for $54?"
  }' | python3 -m json.tool

# Test direct product_id mode (skips LLM intent parsing)
curl -s -X POST "$API_URL/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "U00001",
    "user_query": "Evaluate this product for me",
    "product_id": "B00UXG4WR8"
  }' | python3 -m json.tool
```

**Expected response fields:**
- `recommendation`: `GREEN`, `YELLOW`, or `RED` (from ML model)
- `confidence`: float 0–1 (null if model unavailable)
- `evaluation_mode`: `catalog` (product matched in DB) or `hypothetical`
- `affordability_score`, `emergency_fund_months`, `debt_to_income_ratio`: financial context

> [!NOTE]
> The `/predict` endpoint requires `user_query` (natural language) and `user_id`. `product_id` is optional — if provided, it skips LLM intent parsing and goes directly to product evaluation. User IDs in the database follow the format `U00001`, `U00002`, etc.

### Step 7.2 — Verify CI/CD Triggers Are Working

Make a small, harmless change to test each pipeline trigger:

| Pipeline | Test | Trigger |
|----------|------|---------|
| Data Pipeline | Edit a comment in `data_pipeline/dags/` | Push to `main` |
| Model Pipeline | Edit a comment in `model_pipeline/src/` | Push to `main` |
| Deployment | Edit a comment in `deployment/api/` | Push to `main` |
| Terraform | Edit a comment in `deployment/terraform/` | Open a PR to `main` (plan only) |

### Step 7.3 — Verify DAG Deployment to VM

After a push to `data_pipeline/**` on `main`:
1. The `deploy-dags` job should SSH into the VM
2. Run `git pull` and `airflow dags reserialize`
3. Verify in the Airflow UI that the DAG shows the updated serialization time

### ✅ Phase 7 Checkpoint

- [x] Inference endpoint returns predictions (GREEN/YELLOW/RED with ML confidence score)
- [x] CI/CD auto-triggers on code push to `main` (all three pipelines fire)
- [x] DAG deployment to VM works (`deploy-dags` job SSHes in and reserializes)

---

## Day-to-Day: Running the Data Pipeline

Use these commands whenever you want to start, stop, or trigger the pipeline manually. All commands run from your **local machine** using `gcloud`.

> [!NOTE]
> The VM uses Docker Compose **v1** — always use `docker-compose` (with hyphen), not `docker compose`.

---

### Check VM status & find the current IP

```bash
gcloud compute instances list --project=savvio-purchase-guardrail
# Look for: savvio-dev-pipeline-vm   us-east1-b   RUNNING   <EXTERNAL_IP>
```

The external IP can change after a VM restart. The Airflow UI is always at `http://<EXTERNAL_IP>:8080` (login: `airflow` / `airflow`).

---

### SSH into the VM

```bash
gcloud compute ssh savvio-dev-pipeline-vm \
  --zone=us-east1-b \
  --project=savvio-purchase-guardrail
```

All `docker-compose` commands below can be run either:
- **Directly on the VM** after SSH-ing in, or
- **From your local machine** by appending `--command="..."` to the `gcloud compute ssh` call (see examples below)

---

### Check if containers are running

```bash
# From local machine:
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose ps"
```

Expected healthy services: `postgres`, `redis`, `cloud-sql-proxy`, `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor`, `airflow-worker`, `airflow-triggerer`.

---

### Start the pipeline stack (if containers are stopped)

```bash
# From local machine:
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose up -d"
```

The first start after an image change takes several minutes (rebuilds the image and downloads dependencies). Subsequent starts are fast (~30 seconds).

Wait ~60 seconds after startup, then check health:
```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="curl -s http://localhost:8080/api/v2/version"
```

---

### Stop the pipeline stack

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose down"
```

> [!CAUTION]
> `docker-compose down` stops and removes containers but **preserves volumes** (Airflow metadata DB, logs). Safe to run. Do NOT add `-v` unless you want to wipe all Airflow state.

---

### Restart the worker only (e.g. after a code fix)

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose restart airflow-worker"
```

---

### Pull latest code & restart (manual deploy)

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="sudo git -C /opt/savvio pull origin main && cd /opt/savvio/data_pipeline && sudo docker-compose up -d"
```

> [!NOTE]
> CI/CD (push to `main`) does this automatically via the `deploy-dags` job. Only use this for manual deployments.

---

### Trigger the DAG

**Via Airflow UI (recommended):**
1. Open `http://<VM_EXTERNAL_IP>:8080` in your browser
2. Login: `airflow` / `airflow`
3. Find **Data_pipeline_airflow** → toggle it **ON** → click the ▶ (Trigger) button

**Via CLI from local machine:**
```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose exec -T airflow-apiserver airflow dags trigger Data_pipeline_airflow"
```

---

### Deploy a local code fix to the VM (without pushing to Git)

Use this when you've fixed a DAG source file locally and need it live immediately, without waiting for a Git push + CI/CD cycle.

```bash
# Replace <local_file> with the path from repo root (e.g. data_pipeline/dags/src/database/upload_to_db.py)
# Replace <remote_file> with the same path under /opt/savvio/ on the VM

LOCAL_FILE="data_pipeline/dags/src/database/upload_to_db.py"
REMOTE_FILE="/opt/savvio/data_pipeline/dags/src/database/upload_to_db.py"

# Step 1 — copy to /tmp (no sudo needed for scp)
gcloud compute scp /Users/nirajmehta/Documents/SavVio/$LOCAL_FILE \
  savvio-dev-pipeline-vm:/tmp/$(basename $LOCAL_FILE) \
  --zone=us-east1-b --project=savvio-purchase-guardrail

# Step 2 — move into place with sudo
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="sudo cp /tmp/$(basename $LOCAL_FILE) $REMOTE_FILE && echo 'deployed'"
```

> [!NOTE]
> DAG files under `dags/` are volume-mounted — changes take effect on the next task execution with no restart needed.
> Changes to non-DAG files (e.g. `savviocore/`) require a container restart: `sudo docker-compose restart airflow-worker`.

**Then trigger a new run:**
```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose exec -T airflow-apiserver airflow dags trigger Data_pipeline_airflow"
```

> [!IMPORTANT]
> Remember to also commit and push the fix to Git so the VM stays in sync with the repo. Next `git pull` or CI/CD run will overwrite anything deployed this way.

---

### Clear a stuck / queued DAG run

If tasks are stuck in `queued` state (e.g. after a worker crash):

```bash
# List recent runs to find the run_id
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose exec -T airflow-apiserver airflow dags list-runs -d Data_pipeline_airflow --state queued"

# Clear all queued/running task instances for the latest run
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose exec -T airflow-apiserver airflow tasks clear Data_pipeline_airflow --yes"
```

Then trigger a fresh run via the UI or CLI.

---

### View logs

```bash
# All services (live, last 100 lines):
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose logs --tail=100 -f"

# Worker only (where task execution happens):
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose logs --tail=100 -f airflow-worker"

# Scheduler only:
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --project=savvio-purchase-guardrail \
  --command="cd /opt/savvio/data_pipeline && sudo docker-compose logs --tail=50 airflow-scheduler"
```

---

### Start / stop the VM itself

> Only needed if you want to save costs by shutting down the VM entirely.

```bash
# Stop (saves compute cost, data on persistent disk is preserved):
gcloud compute instances stop savvio-dev-pipeline-vm \
  --zone=us-east1-b --project=savvio-purchase-guardrail

# Start:
gcloud compute instances start savvio-dev-pipeline-vm \
  --zone=us-east1-b --project=savvio-purchase-guardrail
```

After starting, the containers do **not** auto-restart — run `docker-compose up -d` again.

---

## Complete Secret Reference

| # | Secret | Value | Where Used |
|---|--------|-------|------------|
| 1 | `GCP_SA_KEY` | Contents of `gcp-sa-key.json` | All workflows |
| 2 | `GCP_PROJECT_ID` | `savvio-purchase-guardrail` | All workflows |
| 3 | `DB_HOST` | `127.0.0.1` | datapipeline, modelpipeline |
| 4 | `DB_PORT` | `5432` | datapipeline, modelpipeline |
| 5 | `DB_NAME` | `savvio-dev-db` | datapipeline, modelpipeline |
| 6 | `DB_USER` | `dev-db-admin` | datapipeline, modelpipeline |
| 7 | `DB_PASSWORD` | From Secret Manager | datapipeline, modelpipeline |
| 8 | `DB_INSTANCE_CONNECTION_NAME` | `savvio-purchase-guardrail:us-east1:savvio-dev-db-instance` | datapipeline, modelpipeline |
| 9 | `GCE_VM_IP` | From `terraform output pipeline_vm_ip` | datapipeline |
| 10 | `GCE_SSH_PRIVATE_KEY` | Contents of `savvio-vm-key` | datapipeline |
| 11 | `API_URL_DEV` | From `terraform output api_url` | deployment |
| 12 | `SLACK_WEBHOOK_URL` | *(optional)* Slack webhook | deployment |

---

## Troubleshooting

### Terraform apply fails on Cloud Run
**Symptom:** `Revision failed` error on `savvio-dev-api`, `savvio-dev-frontend`, or `savvio-dev-mlflow`.
**Cause:** Placeholder images in `terraform.tfvars`.
**Fix:** The `lifecycle { ignore_changes }` block on the Cloud Run module should prevent this. If it still fails, push a minimal image first:
```bash
# From project root
docker build -t us-east1-docker.pkg.dev/savvio-purchase-guardrail/savvio-dev-docker-repo/savvio-api:latest -f deployment/api/Dockerfile .
docker push us-east1-docker.pkg.dev/savvio-purchase-guardrail/savvio-dev-docker-repo/savvio-api:latest
```
Then update `terraform.tfvars` with the real image URL and re-apply.

### CI/CD DB connection fails
**Symptom:** `connection refused` on `127.0.0.1:5432`.
**Fix:** Verify `DB_INSTANCE_CONNECTION_NAME` secret is set correctly. Format must be `project:region:instance` (e.g. `savvio-purchase-guardrail:us-east1:savvio-dev-db-instance`).

### VM Cloud SQL Proxy fails to start
**Symptom:** `cloud-sql-proxy` Docker container crashes or restarts.
**Fix:** Check the VM's service account has `roles/cloudsql.client`:
```bash
gcloud projects get-iam-policy savvio-purchase-guardrail \
  --flatten="bindings[].members" \
  --filter="bindings.members:savvio-dev-run-sa" \
  --format="value(bindings.role)"
```
Should include `roles/cloudsql.client`.

### DB password has special characters breaking connection strings
**Symptom:** `password authentication failed` on pg_restore or DAG tasks.
**Fix:** URL-encode the password in connection strings, or regenerate with simpler characters:
```bash
# In Terraform modules/cloud_sql/main.tf, change:
override_special = "!#$%&*()-_=+[]{}<>:?"
# To:
override_special = "_-"
```
Then `terraform apply` to regenerate.

### Airflow metadata DB vs Application DB confusion
- **Airflow metadata** → local `postgres` container (user: `airflow`, db: `airflow`) — managed by docker-compose
- **Application data** → Cloud SQL via `cloud-sql-proxy` service (user: `dev-db-admin`, db: `savvio-dev-db`) — used by DAG tasks

These are **completely separate databases**. The docker-compose `environment` block hardcodes the Airflow metadata connection, and the `.env` file configures the application data connection.
