# Setup & Run GitHub Actions Workflows

---

## Part 1 — One-Time Setup (2 GitHub Secrets)

All DB credentials, VM IPs, SSH keys, and API URLs are fetched from GCP at runtime by each workflow. The only manual step is giving the workflows access to GCP.

Go to **Settings > Secrets and variables > Actions** and add:

| Secret | Where to find it |
|--------|-----------------|
| `GCP_SA_KEY` | GCP Console > IAM > Service Accounts > export JSON key for the CI service account |
| `GCP_PROJECT_ID` | Your GCP project ID (e.g. `savvio-purchase-guardrail`) |
| `SLACK_WEBHOOK_URL` | (Optional) Slack incoming webhook URL for drift alerts |

That's it. Everything else is automated.

---

## Part 2 — Running Workflows (GitHub Actions UI)

> **Actions tab** > select workflow > **Run workflow** > choose branch > **Run workflow**

### First-time order

| Step | Workflow | What it does |
|------|----------|--------------|
| 1 | **Terraform Infrastructure** | Provisions all GCP resources: Cloud SQL, Cloud Run services + training job, GCE VM, buckets, SSH keys. The VM auto-configures itself (installs Docker, clones repo, fetches DB creds from Secret Manager, starts Airflow). |
| 2 | **Deployment CI/CD** | Builds API + Frontend Docker images, deploys to Cloud Run. |
| 3 | **Data Pipeline CI/CD** | Validates DAGs, runs unit tests, checks DB connection. |
| 4 | **Model Pipeline CI/CD** | Builds training image, runs Cloud Run training job, validates quality gates. |

After the first run, trigger whichever workflow you need.

### Workflow details

**Terraform Infrastructure** (`terraform.yml`)
- Runs `terraform plan` + `terraform apply`
- Also auto-triggers on pushes to `main` that change `deployment/terraform/**`

**Deployment CI/CD** (`deployment.yml`)
1. API tests + Frontend lint/tests (parallel)
2. Builds Docker images, pushes to Artifact Registry
3. Deploys to Cloud Run via `gcloud run deploy`
4. Verifies API health (`GET /health`)
5. Runs drift detection (Evidently AI)
6. Also runs weekly (Monday 8AM UTC)

**Data Pipeline CI/CD** (`datapipeline_ci.yml`)
1. Unit tests (including `tests/database/`)
2. DAG parse validation
3. DB connection check (credentials fetched from GCP)
4. Data quality check
5. (main only) SSH deploy to VM (SSH key fetched from Secret Manager)

**Model Pipeline CI/CD** (`modelpipeline_ci.yml`)
1. Unit tests
2. DB connection check (credentials fetched from GCP)
3. Builds training Docker image, pushes to Artifact Registry
4. Executes Cloud Run training job (`--wait`)
5. Parses training logs for quality gate metrics
6. Validation gate: F1 >= 0.70, ROC-AUC >= 0.75, bias passed
7. (main only) Triggers Deployment workflow

---

## Part 3 — Troubleshooting

### Cloud Run training job fails immediately
- Check the training image builds locally: `docker build -f model_pipeline/Dockerfile .`
- Verify the job exists: `gcloud run jobs describe savvio-dev-training --region=us-east1`
- Check logs: `gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=savvio-dev-training" --limit=50`

### Validation gate shows 0.0 for all metrics
- The training job may have failed before printing `SAVVIO_METRIC::` lines
- Check the full training logs in the "Fetch training logs" step output

### `gcloud run deploy` fails on first deploy
- Terraform must run first to create the Cloud Run services

### SSH deploy step skipped
- The `deploy-dags` job only runs on `main`. Merge your PR first.

### VM didn't start Airflow automatically
- SSH in and check: `sudo journalctl -u google-startup-scripts`
- Re-run the startup script: `sudo google_metadata_script_runner startup`
