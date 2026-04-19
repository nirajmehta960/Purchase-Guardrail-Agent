# CI/CD Setup — Reproducing the SavVio Pipelines in a New Project

This document is the single source of truth for everything a fresh GCP project
needs in order to make the GitHub Actions workflows under `.github/workflows/`
work end-to-end. Nothing project-specific is hardcoded in the workflows; all
values come from GitHub **Secrets** (sensitive) and **Variables** (non-sensitive,
visible in logs).

> Settings location:
> `GitHub → Settings → Secrets and variables → Actions → {Secrets, Variables}`

---

## 1. Required GitHub Secrets

| Name | Used by | Purpose |
|---|---|---|
| `GCP_PROJECT_ID` | all workflows | GCP project (e.g. `savvio-purchase-guardrail`) |
| `GCP_SA_KEY` | all workflows | JSON key for a service account with Artifact Registry Writer + Cloud Run Admin (kept as a secret because it's a credential) |
| `GCE_VM_IP` | data pipeline deploy | External IP of the Airflow VM |
| `GCE_SSH_PRIVATE_KEY` | data pipeline deploy | Private key matching the `${SSH_USER}` user on the VM |
| `DB_INSTANCE_CONNECTION_NAME` | model pipeline | Cloud SQL connection name (`<proj>:<region>:<instance>`) — used by the Cloud SQL Auth Proxy in `run-pipeline` |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | model pipeline | Application DB credentials used while training in CI |
| `API_URL_DEV` | deployment | Public URL of the deployed API; injected into the frontend image as `VITE_API_BASE` at build time |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | notifications | SMTP relay credentials (Gmail App Password works) |
| `ALERT_EMAIL_FROM` | notifications | Sender address for CI emails |
| `ALERT_EMAIL_LIST` | notifications | Comma-separated recipients |
| `GRAFANA_CLOUD_API_KEY` | terraform | Grafana Cloud API key (passed as `TF_VAR_grafana_api_key`) |
| `GRAFANA_CLOUD_USERNAME` | terraform | Grafana Cloud username / instance ID (`TF_VAR_grafana_cloud_username`) |
| `GRAFANA_REMOTE_WRITE_URL` | terraform | Prometheus remote-write endpoint (`TF_VAR_grafana_remote_write_url`) |

## 2. Required GitHub Variables

| Name | Example value | Purpose |
|---|---|---|
| `PROJECT_NAME` | `SavVio` | Prefix used in CI email subjects |
| `AR_REGION` | `us-east1` | Artifact Registry region |
| `AR_REPO` | `savvio-dev-docker-repo` | Artifact Registry repository name |
| `AR_IMAGE_DATAPIPELINE` | `savvio-data-pipeline` | Image name for the Airflow image |
| `AR_IMAGE_MODELPIPELINE` | `savvio-model-pipeline` | Image name for the ML trainer image |
| `AR_IMAGE_API` | `savvio-api` | Image name for the backend API |
| `AR_IMAGE_FRONTEND` | `savvio-frontend` | Image name for the frontend |
| `AR_IMAGE_MLFLOW` | `savvio-mlflow` | Image name for the MLflow tracking server |
| `VM_DEPLOY_PATH` | `/opt/savvio/data_pipeline` | Path on the Airflow VM that holds `docker-compose.yaml`, `docker-compose.prod.yaml` and `.env` |
| `MONITORING_VM_PATH` | `/home/github-actions/savvio-monitoring` | Optional — path on the GCE VM holding `prometheus.yml` + `docker-compose.production.yml`. Defaults to `/home/${SSH_USER}/savvio-monitoring` |
| `SSH_USER` | `github-actions` | OS user CI uses to SSH into the Airflow VM |
| `AIRFLOW_DAG_ID` | `Data_pipeline_airflow` | DAG to trigger after a successful deploy |
| `ENVIRONMENT` | `dev` | Deployment environment label (appears in CI emails / logs) |
| `MLFLOW_GCS_BUCKET` | `savvio-dev-mlflow-artifacts` | GCS bucket holding `ci/previous_metrics.txt` (rollback baseline) and MLflow artifacts |
| `CLOUD_RUN_API` | `savvio-backend-api` | Cloud Run service updated by the deployment `deploy` job (API) |
| `CLOUD_RUN_FRONTEND` | `savvio-ai` | Cloud Run service for the frontend |
| `CLOUD_RUN_MLFLOW` | `savvio-ai-mlflow` | Cloud Run service hosting the MLflow tracking UI (also used by the model pipeline to resolve `MLFLOW_TRACKING_URI`) |
| `CLOUD_RUN_ML_TRAINER` | `savvio-ai-ml-trainer` | Cloud Run service updated by the model pipeline `deploy` job |
| `RUN_SA_EMAIL` | `savvio-dev-run-sa@<proj>.iam.gserviceaccount.com` | Runtime service account attached to the trainer Cloud Run service |
| `F1_THRESHOLD` | `0.70` | Validation gate — fails if F1 falls below this |
| `ROC_AUC_THRESHOLD` | `0.75` | Validation gate — fails if ROC AUC falls below this |
| `ROLLBACK_THRESHOLD` | `0.02` | Rollback check — fails if F1 drops by more than this vs the previous baseline |
| `NOTIFY_EMAILS_ENABLED` | `true` / `false` | Toggle CI email notifications without changing secrets |
| `TERRAFORM_VERSION` | `1.14.7` | Optional — pin the Terraform CLI version used by the workflow (defaults to `1.14.7` if unset) |
| `MONITORING_VM_PATH` | `/home/github-actions/savvio-monitoring` | Optional — path on the GCE VM holding `prometheus.yml` + `docker-compose.production.yml`. Defaults to `/home/${SSH_USER}/savvio-monitoring` |
| `PROMETHEUS_PROJECT_LABEL` | `savvio` | Optional — value of the `project` external label in `prometheus.production.yml`. Defaults to `vars.PROJECT_NAME` |
| `PROMETHEUS_API_JOB` | `savvio-api` | Optional — Prometheus `job_name`/`service` label for the API target. Defaults to `${PROJECT_NAME}-api` |
| `VITE_DEFAULT_USER_ID` | `U00001` | Default user ID baked into the frontend at build time (`VITE_DEFAULT_USER_ID`); leave empty to ship no fallback |

## 3. One-time GCP setup

1. Enable APIs: Artifact Registry, Compute Engine, Cloud SQL Admin, Cloud Run, IAM.
2. Create a Docker Artifact Registry repo named `${AR_REPO}` in `${AR_REGION}`.
3. Create a service account for CI and grant it:
   - `roles/artifactregistry.writer`
   - `roles/run.admin` (only if the deployment workflow is used)
   - `roles/iam.serviceAccountUser` on the runtime SA used by Cloud Run / GCE
   - Download the JSON key once and store it as `GCP_SA_KEY`.
4. Provision the Airflow VM (Compute Engine, e2-standard-2 minimum). Attach a
   runtime service account that has `roles/cloudsql.client` and
   `roles/secretmanager.secretAccessor` if you use Secret Manager.
5. Terraform-only: create a GCS bucket for the remote state (e.g.
   `<project>-tf-state`) with versioning **enabled**, and update the
   `bucket = "..."` line in
   `deployment/terraform/environments/{dev,prod}/backend.tf` — Terraform backend
   blocks can't read variables, so this is the one place a project-specific
   value lives in code. If a state lock ever gets stuck (interrupted run),
   recover with `terraform force-unlock <ID>` from the environment dir.
6. Then edit `deployment/terraform/environments/{dev,prod}/terraform.tfvars`
   (project ID, region, zone, image placeholders, Cloud Run service names,
   external `data_bucket_name`, and `ci_ssh_public_keys`). All other knobs
   (VM size, network, Cloud Run sizing, source ranges) have sensible
   defaults in `variables.tf` and only need overriding if you want to
   diverge.

### 3.1 Terraform variables you'll likely override

Set these in `terraform.tfvars` (or via `TF_VAR_*` env vars in CI):

| Variable | Why |
|---|---|
| `project_id`, `region`, `zone`, `environment` | Targets the GCP project / region. |
| `name_prefix` | Prepended to every resource name and used as the `project` label. Default `savvio`. |
| `api_service_name`, `frontend_service_name`, `mlflow_service_name` | Cloud Run service names. **Must equal** `vars.CLOUD_RUN_API` / `CLOUD_RUN_FRONTEND` / `CLOUD_RUN_MLFLOW` in GitHub Actions or `gcloud run deploy` from CI silently targets a non-existent service. Set to `null` to fall back to `<prefix>-api` / `-frontend` / `-mlflow`. |
| `data_bucket_name` | Externally-managed GCS bucket the Cloud Run SA gets `objectAdmin` on. Empty string skips the IAM grant. |
| `ci_ssh_public_keys` | List of SSH public keys installed on the pipeline VM for `ci_ssh_user` (default `github-actions`). Must include the public half of `GCE_SSH_PRIVATE_KEY`. |
| `ssh_source_ranges`, `airflow_ui_source_ranges` | CIDRs allowed through the firewall. Default `["0.0.0.0/0"]` — **tighten for prod**. |
| `pipeline_vm_machine_type` / `_disk_image` / `_disk_gb`, `vpc_network` | VM tuning. |
| `api_*` / `frontend_*` / `mlflow_*` (`min_instances`, `max_instances`, `cpu`, `memory`) | Cloud Run sizing per service. |
| `grafana_remote_write_url`, `grafana_cloud_username`, `grafana_api_key` | Pass via `TF_VAR_*` (matching the CI secrets) — never put `grafana_api_key` in `terraform.tfvars`. |

## 4. One-time VM bootstrap

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

# SSH key for the github-actions user (paste the public side that pairs with
# the private key stored in GitHub as GCE_SSH_PRIVATE_KEY).
sudo -u github-actions mkdir -p /home/github-actions/.ssh
sudo -u github-actions tee /home/github-actions/.ssh/authorized_keys < your_pubkey.pub
sudo chmod 600 /home/github-actions/.ssh/authorized_keys

# Allow github-actions to use sudo non-interactively for docker commands only.
echo 'github-actions ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose' \
  | sudo tee /etc/sudoers.d/github-actions
```

Then drop the deployment artefacts into `${VM_DEPLOY_PATH}` once:

```bash
cd /opt/savvio/data_pipeline
# These two are tracked in the repo and can be scp'd or curl'd from GitHub raw:
curl -L -o docker-compose.yaml      https://raw.githubusercontent.com/<org>/SavVio/main/data_pipeline/docker-compose.yaml
curl -L -o docker-compose.prod.yaml https://raw.githubusercontent.com/<org>/SavVio/main/data_pipeline/docker-compose.prod.yaml

# Create the runtime .env on the VM (NEVER commit it).
cat > .env <<'EOF'
DB_INSTANCE_CONNECTION_NAME=<project>:<region>:<instance>
DB_HOST=cloud-sql-proxy
DB_PORT=5432
DB_NAME=...
DB_USER=...
DB_PASSWORD=...
AIRFLOW_UID=50000
_AIRFLOW_WWW_USER_USERNAME=airflow
_AIRFLOW_WWW_USER_PASSWORD=<choose-a-strong-one>
SMTP_HOST=...
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SLACK_WEBHOOK_URL=
EOF

mkdir -p logs config plugins
# Place the GCP service-account JSON key here (it is mounted into Airflow):
sudo cp savvio-gcp-key.json config/
```

> **Why no `vm_env_setup.sh` in the repo?**
> That file historically held the production DB password and was committed
> by accident. `.env` is now produced on the VM by hand and listed in
> `.gitignore`. After this change the password should also be rotated in
> Cloud SQL since the old one is in git history.

## 5. How a deploy actually flows

```
push to main ─►  unit-tests
              └► build-and-push   (docker buildx → AR :sha + :latest)
                 └► deploy        (ssh VM → docker compose pull/up → trigger DAG)
```

The VM never pulls source code; it just pulls the image tag CI just produced
and lets `docker-compose.prod.yaml` strip the host volume mounts so the baked
DAGs win over whatever is on disk.

## 6. Reproducing in another project — checklist

- [ ] Enable APIs and create the Artifact Registry repo
- [ ] Create CI service account + download key
- [ ] Provision the Airflow VM and run the bootstrap above
- [ ] Set the secrets in §1 in the new repo
- [ ] Set the variables in §2 in the new repo
- [ ] Push to `main` and watch `Data Pipeline CI/CD` run

No file under `.github/workflows/` or `data_pipeline/` should need editing.
