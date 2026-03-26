# SavVio Terraform Infrastructure — Setup Guide

## Prerequisites

Before you start, make sure these are installed on your machine:

| Tool | Version | Install |
|------|---------|---------|
| Terraform | >= 1.5 | `brew install terraform` (Mac) or [terraform.io/downloads](https://developer.hashicorp.com/terraform/downloads) |
| Google Cloud SDK (`gcloud`) | Latest | `brew install google-cloud-sdk` or [cloud.google.com/sdk/install](https://cloud.google.com/sdk/docs/install) |
| Git | Any | Already installed on most machines |

No Python venv needed — Terraform is a standalone binary. No `.env` files either — all config lives in `terraform.tfvars`.

---

## Step 0 — Authenticate with GCP

Run these once on your machine. This logs you in and sets the active project.

```bash
# Login to your Google account
gcloud auth login

# Set the project
gcloud config set project savvio-ai

# Create Application Default Credentials (Terraform uses these)
gcloud auth application-default login
```

---

## Step 1 — Clone your repo and navigate to deployment

```bash
git clone https://github.com/<your-org>/SavVio.git
cd SavVio/deployment
```

---

## Step 2 — Download both artifacts from Claude

From the Claude conversation, download these two files:

1. **First artifact** (`SavVio Terraform Infrastructure`) → Save as `deployment/terraform_all.tf`
   - This is a reference file. You don't run it directly — it's for reading.

2. **Second artifact** (`Terraform README + split script`) → Save as `deployment/split_terraform.sh`
   - This is the script that creates all the actual files.

Your folder should look like:

```
SavVio/
└── deployment/
    ├── split_terraform.sh      ← you just downloaded this
    └── terraform_all.tf        ← reference only (optional)
```

---

## Step 3 — Run the split script to generate all Terraform files

```bash
cd SavVio/deployment

# Make the script executable
chmod +x split_terraform.sh

# Run it
bash split_terraform.sh
```

This creates the entire folder structure:

```
deployment/
└── terraform/
    ├── modules/
    │   ├── cloud_sql/
    │   │   ├── main.tf
    │   │   ├── variables.tf
    │   │   └── outputs.tf
    │   ├── cloud_run/
    │   │   ├── main.tf
    │   │   ├── variables.tf
    │   │   └── outputs.tf
    │   ├── storage/
    │   │   ├── main.tf
    │   │   ├── variables.tf
    │   │   └── outputs.tf
    │   ├── artifact_registry/
    │   │   ├── main.tf
    │   │   ├── variables.tf
    │   │   └── outputs.tf
    │   └── secrets/
    │       ├── main.tf
    │       ├── variables.tf
    │       └── outputs.tf
    └── environments/
        ├── dev/
        │   ├── backend.tf
        │   ├── main.tf
        │   ├── variables.tf
        │   ├── outputs.tf
        │   └── terraform.tfvars
        └── prod/
            ├── backend.tf
            ├── main.tf
            ├── variables.tf
            ├── outputs.tf
            └── terraform.tfvars
```

---

## Step 4 — Create the Terraform state bucket (one-time only)

Terraform needs a place to store its state file. This bucket must exist before `terraform init`. Only one person on the team runs this — once ever.

```bash
gsutil mb -p savvio-ai -l us-east1 gs://savvio-ai-tf-state
```

If you get a "bucket already exists" error, that's fine — someone on the team already created it.

---

## Step 5 — Deploy the dev environment

```bash
cd SavVio/deployment/terraform/environments/dev

# Download provider plugins and configure backend
terraform init

# Preview what will be created (no changes made yet)
terraform plan

# Actually create the infrastructure
terraform apply
```

Terraform will show you a plan and ask `Do you want to perform these actions?`. Type `yes` and hit Enter.

**First run takes ~10-15 minutes** (Cloud SQL is slow to provision). Subsequent runs are fast.

---

## Step 6 — Verify the outputs

After `terraform apply` completes, it prints output values:

```
api_url               = "https://savvio-dev-api-xxxxx-ue.a.run.app"
frontend_url          = "https://savvio-dev-frontend-xxxxx-ue.a.run.app"
mlflow_url            = "https://savvio-dev-mlflow-xxxxx-ue.a.run.app"
db_connection_name    = "savvio-ai:us-east1:savvio-dev-db-instance"
dvc_bucket            = "savvio-dev-dvc-data"
mlflow_artifact_bucket = "savvio-dev-mlflow-artifacts"
docker_repo_url       = "us-east1-docker.pkg.dev/savvio-ai/savvio-dev-docker-repo"
```

You can re-print these anytime:

```bash
terraform output
```

---

## Step 7 — Enable pgvector extension (one-time after DB is up)

Terraform creates the PostgreSQL instance, but the `vector` extension needs a SQL command. Run this once after the database is provisioned:

```bash
# Connect via Cloud SQL proxy
gcloud sql connect savvio-dev-db-instance --user=dev-db-admin --database=savvio-dev-db

# At the psql prompt, run:
CREATE EXTENSION IF NOT EXISTS vector;

# Verify it worked
SELECT extname FROM pg_extension WHERE extname = 'vector';

# Exit
\q
```

It will prompt you for the DB password. To retrieve it:

```bash
gcloud secrets versions access latest --secret="savvio-dev-db-password"
```

---

## Step 8 — Configure DVC remote (in your data pipeline)

Now that the bucket exists, point DVC at it:

```bash
cd SavVio/data-pipeline

dvc remote add -d gcs gs://savvio-dev-dvc-data
dvc remote default gcs
```

---

## Step 9 — Deploy prod (when ready)

Same process, different folder:

```bash
cd SavVio/deployment/terraform/environments/prod

terraform init
terraform plan
terraform apply
```

Prod creates the same resources but with stricter settings (IAM auth required, deletion protection on, always-on instances).

---

## How CI/CD Integrates

Your GitHub Actions workflow will:

1. Build Docker images
2. Push them to Artifact Registry
3. Run Terraform with the real image URIs

Example workflow step:

```yaml
- name: Deploy to dev
  run: |
    cd deployment/terraform/environments/dev
    terraform init
    terraform apply -auto-approve \
      -var="api_image=us-east1-docker.pkg.dev/savvio-ai/savvio-dev-docker-repo/api:${{ github.sha }}" \
      -var="frontend_image=us-east1-docker.pkg.dev/savvio-ai/savvio-dev-docker-repo/frontend:${{ github.sha }}" \
      -var="mlflow_image=us-east1-docker.pkg.dev/savvio-ai/savvio-dev-docker-repo/mlflow:${{ github.sha }}"
```

---

## Tearing Down (dev only)

To destroy all dev infrastructure and stop billing:

```bash
cd SavVio/deployment/terraform/environments/dev
terraform destroy
```

Type `yes` when prompted. This deletes everything: Cloud Run services, Cloud SQL, buckets, secrets, registry.

**Never run `terraform destroy` on prod** without team consensus.

---

## Common Issues

| Problem | Fix |
|---------|-----|
| `Error: bucket "savvio-ai-tf-state" does not exist` | Run Step 4 first |
| `Error: googleapi: Error 403: permission denied` | Run `gcloud auth application-default login` again |
| `Error: Cloud Run service placeholder image` | Expected — images deploy via CI/CD. Cloud Run will show "unhealthy" until real images are pushed |
| `Error: project "savvio-ai" not found` | Make sure the GCP project exists and you have Owner/Editor role |
| Terraform is stuck on Cloud SQL | Cloud SQL takes 10-15 min to provision. Be patient. |
| `Error: Secret already exists` | Another team member already applied. Run `terraform import` or coordinate who runs apply. |

---

## File Cheat Sheet

| File | What it does | When you edit it |
|------|-------------|-----------------|
| `terraform.tfvars` | Environment-specific values (region, DB size, images) | When changing config for an environment |
| `variables.tf` | Declares what variables exist | When adding a new configurable value |
| `main.tf` (in env folder) | Wires modules together | When adding/removing infrastructure |
| `modules/*/main.tf` | Reusable resource definitions | When changing how a resource type works |
| `backend.tf` | Where Terraform state is stored | Rarely — only if changing state bucket |
