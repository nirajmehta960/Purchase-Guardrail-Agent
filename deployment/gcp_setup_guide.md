# SavVio — GCP Setup Guide (Step-by-Step)

> **Prerequisites**: GCP account, `gcloud` CLI installed, GitHub repo access.
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
  iap.googleapis.com \
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

### Step 0.4 — Create the Data Bucket

The Airflow DAGs read/write raw and processed data to this bucket. It is referenced by name in the Terraform config but not managed by Terraform — create it once manually:

```bash
gcloud storage buckets create gs://savvio-data-bucket \
  --project=savvio-purchase-guardrail \
  --location=us-east1 \
  --uniform-bucket-level-access
```

### Step 0.5 — Create the GitHub Actions Service Account

```bash
gcloud iam service-accounts create savvio-github-actions \
  --project=savvio-purchase-guardrail \
  --display-name="GitHub Actions CI/CD"
```

### Step 0.6 — Grant IAM Roles to the Service Account

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

### Step 0.7 — Grant SA Access to the TF State Bucket

```bash
gcloud storage buckets add-iam-policy-binding \
  gs://savvio-purchase-guardrail-tf-state \
  --member="serviceAccount:$SA" --role="roles/storage.admin"
```

### Step 0.8 — Download the SA Key

```bash
gcloud iam service-accounts keys create gcp-sa-key.json \
  --iam-account="$SA"
```

> [!CAUTION]
> This file is a long-lived credential. **Never commit it to Git.** You'll paste its contents into GitHub Secrets in Phase 2. After that, store it in a password manager or delete the local copy.

**Verify:**
```bash
cat gcp-sa-key.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Client email: {d[\"client_email\"]}')"
# Expected: Client email: savvio-github-actions@savvio-purchase-guardrail.iam.gserviceaccount.com
```

### Step 0.9 — Generate the GitHub Actions SSH Keypair

CI/CD jobs SSH into the GCE VM to deploy DAGs and Prometheus. Generate a dedicated ed25519 keypair for the `github-actions` user on the VM:

```bash
ssh-keygen -t ed25519 -f savvio-vm-key -C "github-actions@savvio" -N ""
```

This creates two files:
- `savvio-vm-key` — **Private key** → paste into GitHub secret `GCE_SSH_PRIVATE_KEY` (Phase 2)
- `savvio-vm-key.pub` — **Public key** → must be placed in `main.tf` (next step)

**Update `main.tf` with the public key:**

Open `deployment/terraform/environments/dev/main.tf` and find the `metadata_startup_script` block. Replace the `authorized_keys` echo line with your new public key:

```hcl
echo "$(cat savvio-vm-key.pub)" \
  > /home/github-actions/.ssh/authorized_keys
```

The startup script provisions this key into `/home/github-actions/.ssh/authorized_keys` every time the VM boots. This is the only way to provision the key — the VM uses OS Login for gcloud accounts, so the `ssh-keys` instance metadata approach does not apply to the `github-actions` local user.

> [!IMPORTANT]
> If you regenerate the keypair in the future: update `main.tf` with the new public key, commit and push (Terraform CI will apply it, recreating the VM), then update the `GCE_SSH_PRIVATE_KEY` GitHub secret with the new private key.

### ✅ Phase 0 Checkpoint

- [ ] `gs://savvio-purchase-guardrail-tf-state` bucket exists
- [ ] `gs://savvio-data-bucket` bucket exists
- [ ] `savvio-github-actions` SA exists with all 9 roles
- [ ] `gcp-sa-key.json` downloaded locally
- [ ] SSH keypair generated; public key updated in `main.tf`

---

## Phase 1 — Provision Infrastructure with Terraform

### Step 1.1 — Initialize Terraform

```bash
cd deployment/terraform/environments/dev

export GOOGLE_APPLICATION_CREDENTIALS=~/path/to/gcp-sa-key.json

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

**Expected:** ~20 resources to create. Key things to confirm:
- Cloud SQL instance: `savvio-dev-db-instance` (PostgreSQL 15, `db-f1-micro`)
- GCE VM: `savvio-dev-pipeline-vm` (`e2-standard-4`, Debian 12, us-east1-b)
- GCS buckets: `savvio-dev-dvc-data`, `savvio-dev-mlflow-artifacts`
- Cloud Run services: `savvio-backend-api`, `savvio-ai`, `savvio-ai-mlflow` (placeholder images — replaced by CI/CD)
- Service account: `savvio-dev-run-sa`

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
> Copy these values — you'll need them for GitHub Secrets and VM setup.

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
| 1 | `GCP_SA_KEY` | Entire contents of `gcp-sa-key.json` | Phase 0, Step 0.8 |
| 2 | `GCP_PROJECT_ID` | `savvio-purchase-guardrail` | Hardcoded |
| 3 | `DB_HOST` | `127.0.0.1` | CI connects via Cloud SQL Auth Proxy |
| 4 | `DB_PORT` | `5432` | Standard Postgres port |
| 5 | `DB_NAME` | `savvio-dev-db` | Terraform creates this |
| 6 | `DB_USER` | `dev-db-admin` | Terraform creates this |
| 7 | `DB_PASSWORD` | *(from Step 1.5)* | Secret Manager |
| 8 | `DB_INSTANCE_CONNECTION_NAME` | *(from Step 1.4 — `db_connection_name`)* | e.g. `savvio-purchase-guardrail:us-east1:savvio-dev-db-instance` |
| 9 | `GCE_VM_IP` | *(from Step 1.4 — `pipeline_vm_ip`)* | Terraform output |
| 10 | `GCE_SSH_PRIVATE_KEY` | Entire contents of `savvio-vm-key` (private key) | Phase 0, Step 0.9 |
| 11 | `API_URL_DEV` | *(from Step 1.4 — `api_url`)* | Baked into frontend Docker image at build time |
| 12 | `GRAFANA_REMOTE_WRITE_URL` | Grafana Cloud Prometheus remote-write URL | Grafana Cloud → Connections → Prometheus |
| 13 | `GRAFANA_CLOUD_USERNAME` | Grafana Cloud instance numeric ID | Same page — "Username / Instance ID" |
| 14 | `GRAFANA_CLOUD_API_KEY` | Grafana Cloud API key | Grafana Cloud → API Keys |
| 15 | `SMTP_HOST` | SMTP server hostname (e.g. `smtp.gmail.com`) | Email provider |
| 16 | `SMTP_PORT` | SMTP port (typically `587`) | Email provider |
| 17 | `SMTP_USER` | SMTP username | Email provider |
| 18 | `SMTP_PASSWORD` | SMTP password / app password | Email provider |
| 19 | `ALERT_EMAIL_FROM` | From address used in alert emails | Your domain |
| 20 | `ALERT_EMAIL_LIST` | Comma-separated recipient list | Project owners |

> [!NOTE]
> **`DB_HOST=127.0.0.1`** — This is correct for CI. The Cloud SQL Auth Proxy runs as a background process in the runner and listens on `127.0.0.1:5432`. On the GCE VM, the proxy runs as a Docker Compose sidecar and is reachable as `cloud-sql-proxy` (the Docker service name) — these are different values for different environments.

> [!NOTE]
> **`GCE_VM_IP` must be updated after every VM restart.** The VM uses an ephemeral public IP. See the [VM IP Changed](#vm-ip-changed-after-a-restart) runbook in the Day-to-Day section.

### ✅ Phase 2 Checkpoint

- [ ] All 20 secrets added (14 base + 6 SMTP/email for drift + pipeline alerts)
- [ ] `GCP_SA_KEY` contains the full JSON (not just the key ID)
- [ ] `GCE_SSH_PRIVATE_KEY` contains the full private key including `-----BEGIN/END-----` lines

---

## Phase 3 — Set Up the GCE VM

The VM is created by Terraform with Docker installed via startup script. The `github-actions` user and its SSH `authorized_keys` are also provisioned by the startup script.

### Step 3.1 — SSH into the VM

Use your personal gcloud credentials (OS Login) — this is separate from the `github-actions` CI key:

```bash
gcloud compute ssh savvio-dev-pipeline-vm \
  --zone=us-east1-b \
  --project=savvio-purchase-guardrail \
  --tunnel-through-iap
```

> [!NOTE]
> `--tunnel-through-iap` routes SSH through Identity-Aware Proxy instead of directly over the internet. This is the recommended approach and works even if the VM's SSH port is not open to your IP.

### Step 3.2 — Verify the VM is Ready

```bash
# Confirm Docker is installed and running
sudo systemctl status docker

# Confirm github-actions user exists
id github-actions

# Confirm the SSH key was provisioned correctly
sudo cat /home/github-actions/.ssh/authorized_keys
# Expected: the ed25519 public key from Phase 0, Step 0.9
```

If Docker is not running (startup script may still be executing — wait 2–3 minutes and retry):
```bash
sudo systemctl start docker && sudo systemctl enable docker
```

If the `github-actions` user or SSH key is missing (startup script did not complete), run the provisioning manually:
```bash
# Install Docker from official apt repo
sudo apt-get update && sudo apt-get install -y ca-certificates curl gnupg git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable docker && sudo systemctl start docker

# Provision github-actions user
sudo useradd -m -s /bin/bash github-actions
sudo usermod -aG docker github-actions
sudo mkdir -p /home/github-actions/.ssh

# Paste your public key (from savvio-vm-key.pub) on the next line:
echo "ssh-ed25519 AAAA... github-actions@savvio" | sudo tee /home/github-actions/.ssh/authorized_keys
sudo chmod 700 /home/github-actions/.ssh
sudo chmod 600 /home/github-actions/.ssh/authorized_keys
sudo chown -R github-actions:github-actions /home/github-actions/.ssh
```

### Step 3.3 — Clone the Repo

```bash
sudo git clone https://github.com/your-org/SavVio /opt/savvio
sudo chown -R github-actions:github-actions /opt/savvio
```

> Replace `your-org/SavVio` with the actual GitHub repo path.

### Step 3.4 — Create the `.env` File

```bash
# Get the DB password — the VM's service account has Secret Manager access
DB_PASS=$(gcloud secrets versions access latest \
  --secret=savvio-dev-db-password \
  --project=savvio-purchase-guardrail)

sudo tee /opt/savvio/data_pipeline/.env > /dev/null <<EOF
# ---- Application data (Cloud SQL via proxy sidecar) ----
DB_INSTANCE_CONNECTION_NAME=savvio-purchase-guardrail:us-east1:savvio-dev-db-instance
DB_HOST=cloud-sql-proxy
DB_PORT=5432
DB_NAME=savvio-dev-db
DB_USER=dev-db-admin
DB_PASSWORD=${DB_PASS}

# ---- GCS ----
GCS_DATA_BUCKET=savvio-data-bucket

# ---- Airflow settings ----
AIRFLOW_UID=50000
_AIRFLOW_WWW_USER_USERNAME=airflow
_AIRFLOW_WWW_USER_PASSWORD=airflow

# ---- SMTP (Airflow + drift email alerts — required for alerting) ----
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-gmail@gmail.com
SMTP_PASSWORD=your-app-password
ALERT_EMAIL_FROM=alerts@example.com
ALERT_EMAIL_LIST=oncall@example.com,team@example.com
EOF
```

> [!IMPORTANT]
> **`DB_HOST=cloud-sql-proxy`** — This is the Docker Compose service name of the Cloud SQL Auth Proxy sidecar. DAG tasks connect to Cloud SQL through this proxy. Do NOT use `127.0.0.1` here — that value is only for GitHub Actions CI runners where the proxy runs as a background process.

### Step 3.5 — Build and Start Airflow

```bash
cd /opt/savvio/data_pipeline

# Build the custom Airflow image and start all services
sudo docker compose up --build -d
```

The first start takes 3–5 minutes (builds the custom image and runs `airflow db migrate`). Subsequent starts are fast.

Wait for all containers to become healthy:
```bash
sudo docker compose ps
```

Expected healthy services: `postgres`, `redis`, `cloud-sql-proxy`, `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor`, `airflow-worker`, `airflow-triggerer`. The `airflow-init` container exits with code 0 — that is normal.

Verify the API server is responsive:
```bash
curl -s http://localhost:8080/api/v2/version
# Expected: {"version":"3.x.x", ...}
```

The Airflow UI is now available at `http://<VM_EXTERNAL_IP>:8080` (login: `airflow` / `airflow`).

### Step 3.6 — Verify Cloud SQL Proxy Connectivity

```bash
# Test the application DB connection from inside the worker container
sudo docker compose exec airflow-worker python3 -c "
import psycopg2
conn = psycopg2.connect(
    host='cloud-sql-proxy',
    port=5432,
    dbname='savvio-dev-db',
    user='dev-db-admin',
    password='$(gcloud secrets versions access latest --secret=savvio-dev-db-password --project=savvio-purchase-guardrail)'
)
print('Cloud SQL connection OK:', conn.server_version)
conn.close()
"
```

### Step 3.7 — Unpause the DAG

New DAGs default to paused in Airflow. Unpause it so it runs on its `@daily` schedule:

```bash
sudo docker compose exec airflow-apiserver \
  airflow dags unpause Data_pipeline_airflow
```

### ✅ Phase 3 Checkpoint

- [ ] VM SSH works with `gcloud compute ssh --tunnel-through-iap`
- [ ] `github-actions` user exists with correct `authorized_keys`
- [ ] Repo cloned to `/opt/savvio`
- [ ] `.env` created with `DB_HOST=cloud-sql-proxy`
- [ ] `docker compose up -d` succeeded — all containers healthy
- [ ] Cloud SQL proxy connectivity test passed
- [ ] `Data_pipeline_airflow` DAG unpaused

---

## Phase 4 — Populate Cloud SQL with Data

Cloud SQL is empty after Terraform. Choose one option:

### Option A — Run the Airflow DAG (Preferred)

Trigger the DAG once to run the full ingestion pipeline. From the Airflow UI at `http://<VM_IP>:8080`:

1. Go to **DAGs** → `Data_pipeline_airflow`
2. Click the ▶ (Trigger) button

Or via CLI on the VM:
```bash
sudo docker compose exec airflow-apiserver \
  airflow dags trigger Data_pipeline_airflow
```

This runs: **ingestion → preprocessing → validation → feature engineering → DB loading**.

Monitor progress in the Airflow UI. Expected runtime: 10–30 minutes. All task nodes should turn green.

### Option B — Restore from a Local Postgres Dump

Use this if you have an existing database you want to migrate to Cloud SQL.

**Step B.1** — Dump your local database:
```bash
pg_dump -h localhost -U postgres -d savvio \
  --no-owner --no-acl \
  -F c -f savvio_local.dump
```

**Step B.2** — Install Cloud SQL Auth Proxy locally:

macOS (Apple Silicon):
```bash
curl -o cloud-sql-proxy \
  https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.1/cloud-sql-proxy.darwin.arm64
chmod +x cloud-sql-proxy
```

macOS (Intel):
```bash
curl -o cloud-sql-proxy \
  https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.1/cloud-sql-proxy.darwin.amd64
chmod +x cloud-sql-proxy
```

**Step B.3** — Start the proxy in a separate terminal:
```bash
./cloud-sql-proxy \
  savvio-purchase-guardrail:us-east1:savvio-dev-db-instance \
  --port 5433
```

> Port `5433` avoids conflicting with any local Postgres on `5432`.

**Step B.4** — Enable the pgvector extension:
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
> `--no-owner --no-acl` are **required** — Cloud SQL does not allow superuser operations.

**Step B.6** — Verify:
```bash
psql -h 127.0.0.1 -p 5433 -U dev-db-admin -d savvio-dev-db \
  -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
```

Expected tables include: `financial_profiles`, `products`, `reviews`, `product_features`.

### ✅ Phase 4 Checkpoint

- [ ] Cloud SQL has data (either via Airflow DAG or pg_restore)
- [ ] Key tables exist: `financial_profiles`, `products`, `reviews`

---

## Phase 5 — First Model Training

### Step 5.1 — Trigger Model Pipeline

Go to GitHub repo → **Actions** → **Model Pipeline CI/CD** → **Run workflow** → select `main` branch.

On the first run, set **`skip_rollback_check`** = `true` (no previous baseline exists yet).

### Step 5.2 — Monitor the Run

Watch the jobs execute in order:

```
unit-tests          → Should pass (~2 min)
db-connection-check → Should pass (~1 min)
run-pipeline        → Trains XGBoost model (~5–15 min)
validation-gate     → F1 > 0.70, ROC-AUC > 0.75
bias-gate           → Max disparity < 0.10
rollback-check      → Skips automatically (no previous baseline on first run)
persist-metrics     → Saves metrics.txt to GCS as rollback baseline
trigger-deployment  → Dispatches deployment_ci.yml automatically
```

### Step 5.3 — If Gates Fail

- Download the `pipeline-metrics` artifact from the failed run to inspect `metrics.txt` and `bias_metrics.txt`
- F1 < 0.70 or ROC-AUC < 0.75 usually means insufficient training data — verify Phase 4 loaded data correctly
- Bias gate failure: check which demographic slices have high disparity in the bias metrics file

### ✅ Phase 5 Checkpoint

- [ ] All gates passed (validation, bias, rollback)
- [ ] Baseline persisted to `gs://savvio-dev-mlflow-artifacts/ci/previous_metrics.txt`
- [ ] `deployment_ci.yml` automatically triggered

---

## Phase 6 — First Application Deployment

### Step 6.1 — Monitor the Deployment

If `trigger-deployment` in Phase 5 dispatched the workflow, it is already running. Otherwise trigger manually:

GitHub → **Actions** → **Deployment CI/CD** → **Run workflow** → `main` branch.

The workflow runs these jobs:

```
test-api            → API unit tests
test-frontend       → Frontend lint + tests
    ↓ (parallel)
build-push-api      → Builds & pushes API Docker image to Artifact Registry
build-push-frontend → Builds & pushes Frontend Docker image
build-push-mlflow   → Builds & pushes MLflow Docker image
    ↓ (all done)
deploy              → gcloud run deploy for all three services + health check
drift-detection     → Evidently AI drift report (runs against Cloud SQL)
deploy-monitoring   → SSHes to VM, deploys Prometheus docker-compose stack
```

### Step 6.2 — Verify the API

```bash
# Get the live API URL
API_URL=$(gcloud run services describe savvio-backend-api \
  --region=us-east1 --format='value(status.url)')
echo "API URL: $API_URL"

# Health check — must return 200
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

### Step 6.3 — Verify the Frontend

```bash
FRONTEND_URL=$(gcloud run services describe savvio-ai \
  --region=us-east1 --format='value(status.url)')
echo "Frontend URL: $FRONTEND_URL"

curl -s -o /dev/null -w "%{http_code}" "$FRONTEND_URL"
# Expected: 200
```

Open `$FRONTEND_URL` in your browser — the React app should load.

### Step 6.4 — Verify MLflow

```bash
MLFLOW_URL=$(gcloud run services describe savvio-ai-mlflow \
  --region=us-east1 --format='value(status.url)')
echo "MLflow URL: $MLFLOW_URL"
```

Open `$MLFLOW_URL` in your browser — you should see the MLflow tracking UI with the training run logged from Phase 5.

### ✅ Phase 6 Checkpoint

- [ ] Deployment pipeline completed with no errors
- [ ] `/health` returns `{"status":"ok","model":"loaded","db":"connected"}`
- [ ] Frontend loads in browser
- [ ] MLflow UI is accessible and shows the training experiment

---

## Phase 7 — Set Up Grafana Cloud Monitoring

The deployment CI/CD deploys Prometheus to the GCE VM. Prometheus scrapes `/metrics` from the FastAPI service and remote-writes to Grafana Cloud. This phase walks through creating the Grafana Cloud account and wiring up the dashboard.

### Step 7.1 — Create a Grafana Cloud Account

Sign up at [grafana.com/auth/sign-up](https://grafana.com/auth/sign-up). The free tier allows up to 10,000 active series — more than enough.

Your Grafana Cloud stack URL will be `https://<your-org>.grafana.net`.

### Step 7.2 — Get the Prometheus Remote Write Credentials

In Grafana Cloud:

1. Go to your stack → **Connections** → **Add new connection** → search for **Prometheus**
2. Under **"Send metrics to Grafana Cloud"**, find the remote write endpoint details:
   - **Remote Write URL**: e.g. `https://prometheus-prod-56-prod-us-east-2.grafana.net/api/prom/push`
   - **Username / Instance ID**: numeric ID, e.g. `3093100`
3. Generate an API key: **Security** → **API Keys** → **+ Add API key** → Metric Publisher role

Update the GitHub secrets added in Phase 2:
- `GRAFANA_REMOTE_WRITE_URL` → the remote write URL
- `GRAFANA_CLOUD_USERNAME` → the numeric instance ID
- `GRAFANA_CLOUD_API_KEY` → the API key

### Step 7.3 — Import the Dashboard

The dashboard JSON is in the repo at `deployment/monitoring/grafana/provisioning/dashboards/savvio-monitoring.json`.

In Grafana Cloud:
1. Go to **Dashboards** → **Import**
2. Click **Upload JSON file** → select `savvio-monitoring.json`
3. Set the data source to your Grafana Cloud Prometheus instance
4. Click **Import**

### Step 7.4 — Trigger a Deployment to Deploy Prometheus

The Prometheus stack on the VM is deployed by the `deploy-monitoring` job in `deployment_ci.yml`. Trigger the deployment workflow (or push a change to `deployment/**`) to deploy Prometheus:

GitHub → **Actions** → **Deployment CI/CD** → **Run workflow**.

After the workflow completes, verify Prometheus is running on the VM:
```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio-monitoring && sudo docker compose ps"
# Expected: prometheus container running on port 9090
```

Metrics should appear in Grafana Cloud within 1–2 minutes of Prometheus starting.

### ✅ Phase 7 Checkpoint

- [ ] Grafana Cloud account created
- [ ] Remote write credentials in GitHub secrets
- [ ] Dashboard imported and data source configured
- [ ] Prometheus deployed to VM via deployment CI/CD
- [ ] Metrics visible in Grafana dashboard

---

## Phase 8 — Post-Deployment Verification

### Step 8.1 — End-to-End Smoke Test

```bash
# Set API URL
API_URL=$(gcloud run services describe savvio-backend-api \
  --region=us-east1 --format='value(status.url)')

# Test natural language mode
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
- `recommendation`: `GREEN`, `YELLOW`, or `RED`
- `confidence`: float 0–1 (null if model unavailable)
- `evaluation_mode`: `catalog` (product found in DB) or `hypothetical`
- `affordability_score`, `emergency_fund_months`, `debt_to_income_ratio`: financial context

> [!NOTE]
> User IDs in the database follow the format `U00001`, `U00002`, etc.

### Step 8.2 — Verify CI/CD Triggers Are Working

Make a small harmless change to test each pipeline:

| Pipeline | Test change | Expected trigger |
|----------|-------------|-----------------|
| Data Pipeline | Edit a comment in `data_pipeline/dags/` | Push to `main` → datapipeline.yml |
| Model Pipeline | Edit a comment in `model_pipeline/src/` | Push to `main` → modelpipeline.yml |
| Deployment | Edit a comment in `deployment/api/` | Push to `main` → deployment.yml |
| Terraform | Edit a comment in `deployment/terraform/` | Open PR → plan comment on PR; merge → apply |

### Step 8.3 — Verify DAG Deployment to VM

After a push to `data_pipeline/**` on `main`:
1. The `deploy-dags` job SSHes into the VM
2. Runs `git pull` and `airflow dags reserialize`
3. Verify in the Airflow UI that the DAG's "Last Parsed" time updated

### ✅ Phase 8 Checkpoint

- [ ] `/predict` endpoint returns `GREEN`/`YELLOW`/`RED` recommendations
- [ ] All three CI/CD pipelines trigger automatically on code push
- [ ] DAG deployment to VM works (deploy-dags SSHes in and reserializes)
- [ ] Grafana dashboard shows API metrics

---

## Day-to-Day Operations

### Check VM status and find the current IP

```bash
gcloud compute instances list --project=savvio-purchase-guardrail
# Look for: savvio-dev-pipeline-vm   us-east1-b   RUNNING   <EXTERNAL_IP>
```

The Airflow UI is at `http://<EXTERNAL_IP>:8080` (login: `airflow` / `airflow`).

---

### SSH into the VM

```bash
gcloud compute ssh savvio-dev-pipeline-vm \
  --zone=us-east1-b \
  --project=savvio-purchase-guardrail \
  --tunnel-through-iap
```

Commands below can be run either directly on the VM after SSH, or from your local machine by appending `--command="..."` to the `gcloud compute ssh` call.

---

### Check container status

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose ps"
```

Expected healthy services: `postgres`, `redis`, `cloud-sql-proxy`, `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor`, `airflow-worker`, `airflow-triggerer`.

---

### Start the pipeline stack (if containers are stopped)

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose up -d"
```

Wait ~60 seconds then verify:
```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="curl -s http://localhost:8080/api/v2/version"
```

> [!NOTE]
> Containers have `restart: always` in docker-compose — they auto-restart after a VM reboot (stop/start at the OS level). They do NOT restart after `docker compose down` (explicit shutdown). After `docker compose down`, run `docker compose up -d` again.

---

### Stop the pipeline stack

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose down"
```

> [!CAUTION]
> `docker compose down` stops and removes containers but **preserves volumes** (Airflow metadata DB, logs). Do NOT add `-v` unless you want to wipe all Airflow history.

---

### Restart a single service (e.g. after a code fix)

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose restart airflow-worker"
```

---

### Trigger the DAG manually

**Via Airflow UI (recommended):**
1. Open `http://<VM_EXTERNAL_IP>:8080` → login: `airflow` / `airflow`
2. Find **Data_pipeline_airflow** → toggle **ON** if paused → click ▶ Trigger

**Via CLI:**
```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose exec -T airflow-apiserver airflow dags trigger Data_pipeline_airflow"
```

---

### VM IP changed after a restart

The VM uses an ephemeral public IP. After any stop/start or recreation:

```bash
# Get the new IP
NEW_IP=$(gcloud compute instances describe savvio-dev-pipeline-vm \
  --zone=us-east1-b \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
echo "New VM IP: $NEW_IP"

# Update the GitHub secret
gh secret set GCE_VM_IP --body "$NEW_IP"
```

> [!IMPORTANT]
> Until `GCE_VM_IP` is updated, the `deploy-dags` and `deploy-monitoring` CI jobs will fail with `i/o timeout` when trying to SSH.

---

### Pull latest code and restart (manual deploy, bypassing CI)

```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="sudo git -C /opt/savvio pull origin main && cd /opt/savvio/data_pipeline && sudo docker compose up -d"
```

> [!NOTE]
> CI/CD handles this automatically for pushes to `main`. Only use this for out-of-band fixes.

---

### Deploy a single file to the VM without a git push

```bash
LOCAL_FILE="data_pipeline/dags/src/database/upload_to_db.py"
REMOTE_FILE="/opt/savvio/data_pipeline/dags/src/database/upload_to_db.py"

# Copy to /tmp (no sudo needed for scp)
gcloud compute scp /path/to/repo/$LOCAL_FILE \
  savvio-dev-pipeline-vm:/tmp/$(basename $LOCAL_FILE) \
  --zone=us-east1-b --tunnel-through-iap

# Move into place
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="sudo cp /tmp/$(basename $LOCAL_FILE) $REMOTE_FILE"
```

> DAG files under `dags/` are volume-mounted — changes take effect on the next task run with no restart.
> Changes outside `dags/` (e.g. `savviocore/`) require `sudo docker compose restart airflow-worker`.

> [!IMPORTANT]
> Always commit and push the fix to Git afterward. The next `git pull` or CI/CD run will overwrite anything deployed this way.

---

### Clear a stuck or queued DAG run

```bash
# List recent runs
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose exec -T airflow-apiserver airflow dags list-runs -d Data_pipeline_airflow --state queued"

# Clear all queued/running task instances
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose exec -T airflow-apiserver airflow tasks clear Data_pipeline_airflow --yes"
```

---

### View logs

```bash
# All services (last 100 lines, live):
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose logs --tail=100 -f"

# Worker only (where task execution happens):
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose logs --tail=100 -f airflow-worker"

# Scheduler only:
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="cd /opt/savvio/data_pipeline && sudo docker compose logs --tail=50 airflow-scheduler"
```

---

### Stop / start the VM itself

```bash
# Stop (saves compute cost — persistent disk data is preserved):
gcloud compute instances stop savvio-dev-pipeline-vm \
  --zone=us-east1-b --project=savvio-purchase-guardrail

# Start:
gcloud compute instances start savvio-dev-pipeline-vm \
  --zone=us-east1-b --project=savvio-purchase-guardrail
```

After starting, update `GCE_VM_IP` in GitHub secrets with the new IP (see [VM IP Changed](#vm-ip-changed-after-a-restart) above).

---

## Complete Secret Reference

| # | Secret | Value | Used By |
|---|--------|-------|---------|
| 1 | `GCP_SA_KEY` | Contents of `gcp-sa-key.json` | All workflows |
| 2 | `GCP_PROJECT_ID` | `savvio-purchase-guardrail` | All workflows |
| 3 | `DB_HOST` | `127.0.0.1` | datapipeline, modelpipeline, deployment |
| 4 | `DB_PORT` | `5432` | datapipeline, modelpipeline, deployment |
| 5 | `DB_NAME` | `savvio-dev-db` | datapipeline, modelpipeline, deployment |
| 6 | `DB_USER` | `dev-db-admin` | datapipeline, modelpipeline, deployment |
| 7 | `DB_PASSWORD` | From Secret Manager (`savvio-dev-db-password`) | datapipeline, modelpipeline, deployment |
| 8 | `DB_INSTANCE_CONNECTION_NAME` | `savvio-purchase-guardrail:us-east1:savvio-dev-db-instance` | datapipeline, modelpipeline, deployment |
| 9 | `GCE_VM_IP` | Ephemeral VM IP — update after every restart | datapipeline, deployment |
| 10 | `GCE_SSH_PRIVATE_KEY` | Contents of `savvio-vm-key` (private key) | datapipeline, deployment |
| 11 | `API_URL_DEV` | `https://savvio-backend-api-ebw2ryzjkq-ue.a.run.app` | deployment (baked into frontend image) |
| 12 | `GRAFANA_REMOTE_WRITE_URL` | Grafana Cloud Prometheus remote-write URL | deployment |
| 13 | `GRAFANA_CLOUD_USERNAME` | Grafana Cloud instance numeric ID | deployment |
| 14 | `GRAFANA_CLOUD_API_KEY` | Grafana Cloud API key | deployment |
| 15 | `SMTP_HOST` | SMTP host (e.g. `smtp.gmail.com`) | deployment, datapipeline |
| 16 | `SMTP_PORT` | SMTP port (typically `587`) | deployment, datapipeline |
| 17 | `SMTP_USER` | SMTP username | deployment, datapipeline |
| 18 | `SMTP_PASSWORD` | SMTP password / app password | deployment, datapipeline |
| 19 | `ALERT_EMAIL_FROM` | From address used in alert emails | deployment |
| 20 | `ALERT_EMAIL_LIST` | Comma-separated recipient list | deployment |

---

## Troubleshooting

### SSH `i/o timeout` from CI/CD

**Symptom:** `deploy-dags` or `deploy-monitoring` job fails with `dial tcp: i/o timeout`.
**Cause:** `GCE_VM_IP` secret is stale — the VM IP changed after a restart.
**Fix:** Get the new IP and update the secret:
```bash
gh secret set GCE_VM_IP --body "$(gcloud compute instances describe savvio-dev-pipeline-vm \
  --zone=us-east1-b --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"
```

### SSH `unable to authenticate` from CI/CD

**Symptom:** `deploy-dags` job fails with `unable to authenticate, attempted methods [none publickey]`.
**Cause:** The `github-actions` SSH `authorized_keys` on the VM is missing or has the wrong public key.
**Fix:** SSH in as yourself and verify/restore the key:
```bash
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b --tunnel-through-iap \
  --command="sudo cat /home/github-actions/.ssh/authorized_keys"
```
If the key is wrong or missing, paste the correct public key from `savvio-vm-key.pub` into `authorized_keys`, or run `terraform apply` to re-provision the VM (the startup script will restore it).

### Terraform apply fails with `409 AlreadyExists` on Cloud Run

**Symptom:** `Error creating Service: googleapi: Error 409: Resource already exists`.
**Cause:** The Cloud Run service was created via `gcloud run deploy` (by CI/CD) but is not tracked in Terraform state.
**Fix:** Import the existing service into Terraform state:
```bash
cd deployment/terraform/environments/dev
terraform import module.api.google_cloud_run_v2_service.service \
  projects/savvio-purchase-guardrail/locations/us-east1/services/savvio-backend-api
```
Repeat for `savvio-ai` and `savvio-ai-mlflow` if needed, substituting the module path and service name.

### Terraform causes new Cloud Run revisions on every apply

**Symptom:** `terraform plan` shows changes to `client`/`client_version` even when no code changed. Each apply triggers a new Cloud Run revision.
**Cause:** `gcloud run deploy` sets these metadata fields; Terraform detects them as drift.
**Fix:** Already handled — `deployment/terraform/modules/cloud_run/main.tf` has `lifecycle { ignore_changes = [template[0].containers[0].image, client, client_version] }`. If you see this on a new environment, verify that block is present.

### Stale Terraform state lock

**Symptom:** `Error acquiring the state lock: ConditionalCheckFailedException`.
**Cause:** A previous Terraform run was interrupted and left a lock file in GCS.
**Fix:** The `terraform.yml` workflow auto-unlocks stale locks before every apply. For manual runs:
```bash
LOCK_ID=$(gcloud storage cat \
  gs://savvio-purchase-guardrail-tf-state/env/dev/default.tflock \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('ID',''))")
terraform force-unlock -force "$LOCK_ID"
```

### CI/CD DB connection fails (`connection refused`)

**Symptom:** DB connection check fails with `connection refused` on `127.0.0.1:5432`.
**Cause:** Usually the `DB_INSTANCE_CONNECTION_NAME` secret is wrong, or the Cloud SQL Auth Proxy failed to start.
**Fix:** Verify the secret format is exactly `project:region:instance`:
```
savvio-purchase-guardrail:us-east1:savvio-dev-db-instance
```
Check there are no spaces or newlines in the secret value.

### Cloud SQL connection exhaustion

**Symptom:** `FATAL: remaining connection slots are reserved for non-replication superuser connections`.
**Cause:** `db-f1-micro` allows ~25 connections. Multiple Cloud Run revisions + MLflow gunicorn workers can exhaust this.
**Fix:** Delete old Cloud Run revisions to free connections:
```bash
gcloud run revisions list --service=savvio-ai-mlflow --region=us-east1
gcloud run revisions delete <old-revision-name> --region=us-east1 --quiet
```
The MLflow service is already configured with `--workers 1` and `pool_size=2&max_overflow=0` to limit connections per instance.

### VM Cloud SQL Proxy container crashes

**Symptom:** `cloud-sql-proxy` container restarts repeatedly; Airflow tasks fail with connection errors.
**Cause:** The VM's service account (`savvio-dev-run-sa`) may be missing `roles/cloudsql.client`.
**Fix:**
```bash
gcloud projects get-iam-policy savvio-purchase-guardrail \
  --flatten="bindings[].members" \
  --filter="bindings.members:savvio-dev-run-sa" \
  --format="value(bindings.role)"
# Must include: roles/cloudsql.client
```
If missing:
```bash
gcloud projects add-iam-policy-binding savvio-purchase-guardrail \
  --member="serviceAccount:savvio-dev-run-sa@savvio-purchase-guardrail.iam.gserviceaccount.com" \
  --role="roles/cloudsql.client"
```

### Airflow metadata DB vs Application DB confusion

Two separate databases:

| | Airflow Metadata DB | Application Data DB |
|---|---|---|
| **Purpose** | Stores DAG runs, task state, Airflow config | Stores financial profiles, products, reviews |
| **Host** | `postgres` Docker service (local container) | `cloud-sql-proxy` Docker service → Cloud SQL |
| **User** | `airflow` | `dev-db-admin` |
| **Database** | `airflow` | `savvio-dev-db` |
| **Set in** | `docker-compose.yaml` environment block (hardcoded) | `.env` file on VM |

Never point `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN` at Cloud SQL — Airflow's own metadata stays local.

### DB password with special characters breaks connection strings

**Symptom:** `password authentication failed` despite correct credentials; Python `urllib.parse` errors.
**Cause:** Terraform generates passwords with symbols that break URI parsing.
**Fix:** The application code URL-encodes passwords before building connection URIs (see `deployment/mlflow/entrypoint.sh`). If a raw psql or pg_restore call fails, URL-encode manually:
```bash
python3 -c "import urllib.parse; print(urllib.parse.quote('<your-password>', safe=''))"
```
