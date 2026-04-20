# SavVio — Reproducing the GCP Deployment

This guide lets a teammate fork this repo and run the full **data pipeline CI/CD** in **their own GCP project**, with no code changes required.

Everything project-specific is expressed as **GitHub repository variables/secrets** and **GCP Secret Manager entries** — the workflow itself is project-agnostic.

---

## 0. Prerequisites

Install locally:

| Tool | Purpose |
|---|---|
| `gcloud` CLI (authenticated) | Provision GCP resources, populate Secret Manager |
| `terraform` ≥ 1.5 | Bring up VM / Cloud SQL / Artifact Registry / GCS |
| `gh` CLI *(optional)* | Set GitHub repo vars/secrets from the terminal |
| `ssh` | Diagnose the VM if CI fails |

Assumptions: you have **Owner** (or equivalent) on the target GCP project and **admin** on the GitHub fork.

---

## 1. Create the GCP project

```bash
export GCP_PROJECT_ID="<your-project-id>"
gcloud projects create "$GCP_PROJECT_ID" --name="SavVio Reproduction"
gcloud config set project "$GCP_PROJECT_ID"

# Enable required APIs
gcloud services enable \
  compute.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com
```

Link a billing account if this is a brand-new project.

---

## 2. Provision infrastructure with Terraform

```bash
cd deployment/terraform/environments/dev
# Edit terraform.tfvars (or use -var) to point at $GCP_PROJECT_ID
terraform init
terraform apply
```

The modules in `deployment/terraform/modules/` provision:
- Artifact Registry repo
- Airflow VM (GCE instance) and its instance service account
- Cloud SQL (Postgres) and the Cloud SQL Proxy config
- GCS bucket for raw / processed / features / validated data
- Secret Manager resources (empty versions — filled in step 3)

Capture the outputs you'll need for step 5:

```bash
terraform output -raw vm_ip
terraform output -raw vm_service_account_email
terraform output -raw ar_repo_name
terraform output -raw gcs_bucket_name
```

---

## 3. Populate Secret Manager

A helper script is included that prompts once for each secret value.

```bash
# From the repo root
bash data_pipeline/scripts/setup_secrets.sh "$GCP_PROJECT_ID"
# Or with a custom namespace:
bash data_pipeline/scripts/setup_secrets.sh "$GCP_PROJECT_ID" myproj
```

It upserts (creates or adds a new version of) these 12 secrets:

| Secret name (default prefix `savvio`) | Used for |
|---|---|
| `savvio-db-user` / `-db-password` / `-db-host` / `-db-port` / `-db-name` | Cloud SQL connection |
| `savvio-smtp-host` / `-smtp-port` / `-smtp-user` / `-smtp-password` | Airflow alert emails |
| `savvio-alert-email-from` / `-alert-email-list` | Alert email routing |
| `savvio-airflow-www-password` | Airflow UI admin password |

The prefix (`savvio` by default) is configurable — see step 5, `SECRET_PREFIX`.

---

## 4. Grant the VM service account access

The Airflow VM authenticates to GCP via its **instance service account** (no key files on disk). Grant it the two roles the workflow needs:

```bash
VM_SA="$(terraform -chdir=deployment/terraform/environments/dev output -raw vm_service_account_email)"

gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:${VM_SA}" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:${VM_SA}" \
  --role="roles/artifactregistry.reader"
```

*(If the Terraform modules already add these bindings, this step is a no-op — just run it to be safe.)*

---

## 5. Configure GitHub

Go to **Repo → Settings → Secrets and variables → Actions**.

### 5a. Secrets (encrypted; used by CI only)

| Secret | Required | Description |
|---|---|---|
| `GCE_VM_IP` | ✅ | Public IP of the Airflow VM (from Terraform). |
| `GCE_SSH_PRIVATE_KEY` | ✅ | Private key whose public half is authorised on the VM user. |
| `GCP_SA_KEY` | ✅ | JSON key of a CI-only SA with `roles/artifactregistry.writer`. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | optional* | Only needed if `NOTIFY_EMAILS_ENABLED=true`. |
| `ALERT_EMAIL_FROM` / `ALERT_EMAIL_LIST` | optional* | Only needed if `NOTIFY_EMAILS_ENABLED=true`. |

<sub>* These duplicate Secret Manager values because notification jobs run on the GitHub runner, which has no metadata-server access.</sub>

### 5b. Variables (plaintext; discoverable in the UI)

**Required** — no defaults:

| Variable | Example |
|---|---|
| `GCP_PROJECT_ID` | `my-gcp-project-id` |
| `AR_REGION` | `us-east1` |
| `AR_REPO` | `savvio-dev-docker-repo` |
| `AR_IMAGE_DATAPIPELINE` | `savvio-data-pipeline` |
| `SSH_USER` | `github-actions` |
| `VM_DEPLOY_PATH` | `/home/github-actions/savvio-data-pipeline` |
| `AIRFLOW_DAG_ID` | `Data_pipeline_airflow` |
| `PROJECT_NAME` | `SavVio` |
| `NOTIFY_EMAILS_ENABLED` | `true` or `false` |

**Optional** — each has a default and only needs to be set if you diverge:

| Variable | Default | Why change it |
|---|---|---|
| `SECRET_PREFIX` | `savvio` | You passed a non-default prefix to `setup_secrets.sh`. |
| `GCS_BUCKET_NAME` | `savvio-data-bucket` | Your bucket has a different name. |
| `VERTEX_LOCATION` | `us-east1` | Vertex AI is in a different region. |
| `GCP_CREDENTIALS_PATH` | `/opt/airflow/config/savvio-gcp-key.json` | Your SA key file lives at a different path inside the container. |

### 5c. Fast setup via `gh` CLI

```bash
gh variable set GCP_PROJECT_ID          -b "$GCP_PROJECT_ID"
gh variable set AR_REGION               -b "us-east1"
gh variable set AR_REPO                 -b "$(terraform -chdir=deployment/terraform/environments/dev output -raw ar_repo_name)"
gh variable set AR_IMAGE_DATAPIPELINE   -b "savvio-data-pipeline"
gh variable set SSH_USER                -b "github-actions"
gh variable set VM_DEPLOY_PATH          -b "/home/github-actions/savvio-data-pipeline"
gh variable set AIRFLOW_DAG_ID          -b "Data_pipeline_airflow"
gh variable set PROJECT_NAME            -b "SavVio"
gh variable set NOTIFY_EMAILS_ENABLED   -b "false"

gh secret   set GCE_VM_IP               -b "$(terraform -chdir=deployment/terraform/environments/dev output -raw vm_ip)"
gh secret   set GCE_SSH_PRIVATE_KEY     < path/to/private_key
gh secret   set GCP_SA_KEY              < path/to/ci_sa_key.json
```

---

## 6. Ship it

```bash
git push origin main
```

The `Data Pipeline CI/CD` workflow will:

1. Run unit tests.
2. Build the Airflow image and push to Artifact Registry.
3. SSH to the VM, write a fresh `.env` from Secret Manager, sync docker-compose files, and run `docker compose up -d`.
4. Wait for the scheduler, worker, and your DAG to become ready; then trigger a run.

---

## 7. Verifying success

```bash
# 1. CI succeeded?
gh run list --workflow=datapipeline.yml --limit=1

# 2. DAG is registered on the VM?
gcloud compute ssh <VM_NAME> --zone=<ZONE> --project="$GCP_PROJECT_ID" \
  --tunnel-through-iap --command="
    cd $VM_DEPLOY_PATH && \
    sudo -u github-actions docker compose \
      -f docker-compose.yaml -f docker-compose.prod.yaml \
      --env-file .env --env-file .env.ci \
      exec -T airflow-scheduler airflow dags list"

# 3. Open the Airflow UI
# http://<VM_PUBLIC_IP>:8080  —  login: airflow / <value of savvio-airflow-www-password>
```

---

## 8. What to change when forking

The **only** files that should need editing for a fork are:

1. `deployment/terraform/environments/<env>/terraform.tfvars` — pick your own project/region/names.
2. Your **GitHub repo variables** (step 5b) — reflect the Terraform outputs.

If you find yourself wanting to edit `.github/workflows/*.yml` or `data_pipeline/scripts/setup_secrets.sh` just to change a hard-coded name, that's a bug — please open a PR promoting that value to a variable.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ERROR: (gcloud.secrets.versions.access) PERMISSION_DENIED` during `Write .env from Secret Manager` | VM SA missing `secretmanager.secretAccessor` | Re-run step 4. |
| `Error response from daemon: ... Unauthenticated request` when pulling the image | VM SA missing `artifactregistry.reader` | Re-run step 4. |
| `.env` exists on VM but `DB_PASSWORD=` is empty | A secret is missing. `inherit_errexit` now fails fast — check which `fetch` call exited. | Re-run `setup_secrets.sh` and supply the missing value. |
| Airflow UI shows "No DAGs found" after a green CI run | DAG files aren't in the image (check `docker exec ... ls /opt/airflow/dags`). | Make sure `COPY data_pipeline/dags /opt/airflow/dags` is present in `data_pipeline/Dockerfile`. |
| `No data found` from `airflow dags list` | Same as above, or import errors. Run `airflow dags list-import-errors`. | Fix import errors in the DAG file. |

---

## Appendix: Secret / variable cheat sheet

```text
REQUIRED GITHUB SECRETS:     REQUIRED GITHUB VARS:        GCP SECRET MANAGER:
  GCE_VM_IP                    GCP_PROJECT_ID              ${PREFIX}-db-user
  GCE_SSH_PRIVATE_KEY          AR_REGION                   ${PREFIX}-db-password
  GCP_SA_KEY                   AR_REPO                     ${PREFIX}-db-host
                               AR_IMAGE_DATAPIPELINE       ${PREFIX}-db-port
OPTIONAL (notifications):      SSH_USER                    ${PREFIX}-db-name
  SMTP_HOST, SMTP_PORT         VM_DEPLOY_PATH              ${PREFIX}-smtp-host
  SMTP_USER, SMTP_PASSWORD     AIRFLOW_DAG_ID              ${PREFIX}-smtp-port
  ALERT_EMAIL_FROM             PROJECT_NAME                ${PREFIX}-smtp-user
  ALERT_EMAIL_LIST             NOTIFY_EMAILS_ENABLED       ${PREFIX}-smtp-password
                                                           ${PREFIX}-alert-email-from
                             OPTIONAL VARS (have defaults):${PREFIX}-alert-email-list
                               SECRET_PREFIX               ${PREFIX}-airflow-www-password
                               GCS_BUCKET_NAME
                               VERTEX_LOCATION             REQUIRED VM SA IAM:
                               GCP_CREDENTIALS_PATH          roles/secretmanager.secretAccessor
                                                             roles/artifactregistry.reader
```
