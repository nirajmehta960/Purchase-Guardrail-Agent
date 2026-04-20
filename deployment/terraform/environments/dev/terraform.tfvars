project_id  = "savvio-purchase-guardrail"
region      = "us-east1"
zone        = "us-east1-d"
environment = "dev"
db_tier     = "db-f1-micro"

# Placeholder images — CI/CD overrides these via: terraform apply -var="api_image=..."
api_image      = "gcr.io/cloudrun/placeholder"
frontend_image = "gcr.io/cloudrun/placeholder"
mlflow_image   = "gcr.io/cloudrun/placeholder"

# Cloud Run service names — these MUST equal vars.CLOUD_RUN_API / CLOUD_RUN_FRONTEND
# / CLOUD_RUN_MLFLOW in GitHub Actions, or `gcloud run deploy` from the deployment
# workflow will silently target a non-existent service.
api_service_name      = "savvio-backend-api"
frontend_service_name = "savvio-ai"
mlflow_service_name   = "savvio-ai-mlflow"

# Externally-managed bucket the Airflow DAGs write to (created outside Terraform).
data_bucket_name = "savvio-data-bucket"

# CI SSH access — public key whose private half is in GitHub Secrets as
# GCE_SSH_PRIVATE_KEY. Replace for a new project.
ci_ssh_public_keys = [
  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGqm1rC02mc0wK/jeU/e8TUR1Thuw9P2cRO6vutMiRhw github-actions@savvio",
]
