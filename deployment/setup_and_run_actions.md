# Setup & Run GitHub Actions Workflows

This guide walks through the one-time setup required to run each workflow, then how to trigger them.

---

## Prerequisites

1. A GCP project with billing enabled
2. The `gcloud` CLI installed locally (for initial bootstrapping)
3. Admin access to the GitHub repository (to configure secrets)

---

## 1. GCP Service Account

All workflows authenticate via a single service account JSON key.

```bash
# Create the service account
gcloud iam service-accounts create savvio-ci \
  --display-name="SavVio CI/CD"

# Grant required roles
for ROLE in \
  roles/run.admin \
  roles/run.invoker \
  roles/artifactregistry.writer \
  roles/cloudsql.client \
  roles/storage.objectAdmin \
  roles/compute.instanceAdmin.v1 \
  roles/secretmanager.secretAccessor \
  roles/iam.serviceAccountUser \
  roles/logging.viewer; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:savvio-ci@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="$ROLE"
done

# Export the key
gcloud iam service-accounts keys create sa-key.json \
  --iam-account=savvio-ci@$PROJECT_ID.iam.gserviceaccount.com
```

---

## 2. GitHub Secrets

Go to **Settings > Secrets and variables > Actions** and add:

| Secret | Value | Used by |
|--------|-------|---------|
| `GCP_SA_KEY` | Contents of `sa-key.json` | All workflows |
| `GCP_PROJECT_ID` | Your GCP project ID | All workflows |
| `DB_HOST` | Cloud SQL public IP (from Terraform output or GCP Console) | datapipeline, modelpipeline |
| `DB_PORT` | `5432` | datapipeline, modelpipeline |
| `DB_NAME` | Database name (e.g. `savvio-dev-db`) | datapipeline, modelpipeline |
| `DB_USER` | Database user (e.g. `dev-db-admin`) | datapipeline, modelpipeline |
| `DB_PASSWORD` | From GCP Secret Manager `savvio-dev-db-password` | datapipeline, modelpipeline |
| `GCE_VM_IP` | Pipeline VM external IP (from Terraform output) | datapipeline |
| `GCE_SSH_PRIVATE_KEY` | SSH private key for the pipeline VM | datapipeline |
| `API_URL_DEV` | Cloud Run API URL (from Terraform output) | deployment |
| `SLACK_WEBHOOK_URL` | (Optional) Slack incoming webhook for drift alerts | deployment |

---

## 3. Terraform — Provision Infrastructure

Terraform creates all GCP resources: Cloud SQL, Cloud Run services, the training Cloud Run job, the GCE VM, Artifact Registry, GCS buckets, and secrets.

```bash
cd deployment/terraform/environments/dev

# Initialize
terraform init

# Preview
terraform plan -var-file="terraform.tfvars"

# Apply
terraform apply -var-file="terraform.tfvars"

# Note the outputs — you'll need them for GitHub secrets
terraform output
```

After apply, grab the values for `DB_HOST`, `pipeline_vm_ip`, and `api_url` and add them to GitHub secrets.

---

## 4. Pipeline VM Setup (one-time)

SSH into the GCE VM and set up Airflow:

```bash
# SSH in (IP from terraform output pipeline_vm_ip)
gcloud compute ssh savvio-dev-pipeline-vm --zone=us-east1-b

# Clone the repo
sudo mkdir -p /opt/savvio && cd /opt/savvio
sudo git clone https://github.com/<your-org>/SavVio.git .

# Create .env with DB credentials
cat > .env << 'EOF'
DB_HOST=<cloud-sql-ip>
DB_PORT=5432
DB_NAME=savvio-dev-db
DB_USER=dev-db-admin
DB_PASSWORD=<password-from-secret-manager>
EOF

# Start Airflow
docker-compose up -d
```

---

## 5. SSH Key for Data Pipeline Deploy

The `datapipeline_ci.yml` workflow SSHes into the VM on main merges to update DAGs.

```bash
# Generate a key pair locally
ssh-keygen -t ed25519 -f savvio-deploy-key -N ""

# Add public key to VM metadata
gcloud compute instances add-metadata savvio-dev-pipeline-vm \
  --zone=us-east1-b \
  --metadata-from-file=ssh-keys=<(echo "savvio:$(cat savvio-deploy-key.pub)")

# Add private key as GitHub secret GCE_SSH_PRIVATE_KEY
cat savvio-deploy-key
# Copy and paste into GitHub Secrets
```

---

## 6. Running the Workflows

All workflows use `workflow_dispatch` (manual trigger). Go to **Actions** tab in GitHub and select the workflow to run.

### Data Pipeline CI (`datapipeline_ci.yml`)

**What it does:**
1. Unit tests (including `tests/database/`)
2. DAG parse validation
3. DB connection check
4. Data quality check (validation tests)
5. (main only) SSH deploy — pulls latest code on VM and reserializes DAGs

**Trigger:** Actions > Data Pipeline CI/CD > Run workflow

### Model Pipeline CI (`modelpipeline_ci.yml`)

**What it does:**
1. Unit tests
2. DB connection check
3. Builds training Docker image, pushes to Artifact Registry
4. Executes `savvio-dev-training` Cloud Run job (`--wait` blocks until done)
5. Parses training logs for metrics (`SAVVIO_METRIC::` lines)
6. Validation gate: F1 >= 0.70, ROC-AUC >= 0.75, bias passed
7. (main only) Triggers deployment workflow on success

**Trigger:** Actions > Model Pipeline CI/CD > Run workflow

**First run note:** Terraform creates the Cloud Run job with a placeholder image. The first CI run will update the job image and then execute it.

### Deployment CI (`deployment.yml`)

**What it does:**
1. API tests + Frontend lint/tests (parallel)
2. Builds API + Frontend Docker images, pushes to Artifact Registry (parallel)
3. Deploys both to Cloud Run via `gcloud run deploy`
4. Verifies API health (`GET /health`)
5. Runs drift detection (Evidently AI) after deploy

**Trigger:** Actions > Deployment CI/CD > Run workflow

Also runs on a weekly schedule (Monday 8AM UTC).

### Terraform (`terraform.yml`)

**What it does:**
- `terraform plan` and `terraform apply` on the dev environment

**Trigger:** Actions > Terraform Infrastructure > Run workflow

Also auto-triggers on pushes to `main` that change `deployment/terraform/**`.

---

## 7. Recommended Run Order (First Time)

1. **Terraform** — provisions all infrastructure
2. **Pipeline VM setup** — manual SSH setup (step 4 above)
3. **Deployment CI** — builds and deploys API + Frontend images (replaces placeholder images)
4. **Data Pipeline CI** — validates pipeline code, connects to DB
5. **Model Pipeline CI** — trains the model via Cloud Run, validates quality gates

---

## 8. Troubleshooting

### Cloud Run job fails immediately
- Check the training image builds correctly: `docker build -f model_pipeline/Dockerfile .`
- Verify the Cloud Run job exists: `gcloud run jobs describe savvio-dev-training --region=us-east1`
- Check logs: `gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=savvio-dev-training" --limit=50`

### Validation gate shows 0.0 for all metrics
- The training job may have failed before printing `SAVVIO_METRIC::` lines
- Check the full training logs in the "Fetch training logs" step output
- Ensure `DB_HOST` / `DB_PASSWORD` secrets are set (training needs DB access for feature engineering)

### `gcloud run deploy` fails on first deploy
- The Terraform step must run first to create the Cloud Run services
- If deploying without Terraform, create the services manually first:
  ```bash
  gcloud run deploy savvio-dev-api --image gcr.io/cloudrun/placeholder --region us-east1
  ```

### SSH deploy step skipped
- The `deploy-dags` job only runs on `main` branch. Merge your PR first.
- Verify `GCE_VM_IP` and `GCE_SSH_PRIVATE_KEY` secrets are set.
