terraform {
  required_version = ">= 1.14.7"
  required_providers {
    google = { source = "hashicorp/google"
    version = "~> 7.25.0" }
    random = { source = "hashicorp/random"
    version = "~> 3.8.1" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {}

locals {
  prefix = "${var.name_prefix}-${var.environment}"
  labels = {
    project     = var.name_prefix
    environment = var.environment
    managed_by  = "terraform"
  }

  api_service_name      = coalesce(var.api_service_name, "${local.prefix}-api")
  frontend_service_name = coalesce(var.frontend_service_name, "${local.prefix}-frontend")
  mlflow_service_name   = coalesce(var.mlflow_service_name, "${local.prefix}-mlflow")
}

# ---- APIs ----
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "run.googleapis.com",
    "iam.googleapis.com",
    "aiplatform.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ---- Service Account ----
resource "google_service_account" "cloud_run" {
  account_id   = "${local.prefix}-run-sa"
  display_name = "${var.name_prefix} ${var.environment} Cloud Run SA"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "run_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "run_vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_storage_bucket_iam_member" "run_mlflow_storage" {
  bucket = module.mlflow_bucket.bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_storage_bucket_iam_member" "run_dvc_storage" {
  bucket = module.dvc_bucket.bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

# ---- Cloud SQL ----
module "database" {
  source              = "../../modules/cloud_sql"
  instance_name       = "${local.prefix}-db-instance"
  region              = var.region
  tier                = var.db_tier
  database_name       = "${local.prefix}-db"
  user_name           = "${var.environment}-db-admin"
  deletion_protection = true
  labels              = local.labels
  depends_on          = [google_project_service.apis]
}

# ---- Secrets ----
module "db_password_secret" {
  source                         = "../../modules/secrets"
  secret_id                      = "${local.prefix}-db-password"
  secret_data                    = module.database.password
  accessor_service_account_email = google_service_account.cloud_run.email
  depends_on                     = [google_project_service.apis]
}

module "grafana_api_key_secret" {
  source                         = "../../modules/secrets"
  secret_id                      = "${local.prefix}-grafana-api-key"
  secret_data                    = var.grafana_api_key != "" ? var.grafana_api_key : "not-configured"
  accessor_service_account_email = google_service_account.cloud_run.email
  depends_on                     = [google_project_service.apis]
}

# ---- Storage ----
module "dvc_bucket" {
  source        = "../../modules/storage"
  bucket_name   = "${local.prefix}-dvc-data"
  location      = var.region
  force_destroy = false
  labels        = local.labels
}

module "mlflow_bucket" {
  source        = "../../modules/storage"
  bucket_name   = "${local.prefix}-mlflow-artifacts"
  location      = var.region
  force_destroy = false
  labels        = local.labels
}

# ---- Artifact Registry ----
module "docker_repo" {
  source        = "../../modules/artifact_registry"
  repository_id = "${local.prefix}-docker-repo"
  location      = var.region
  labels        = local.labels
  depends_on    = [google_project_service.apis]
}

# ---- GCE VM (Airflow + ML Training) ----
resource "google_compute_instance" "pipeline_vm" {
  name         = "${local.prefix}-pipeline-vm"
  machine_type = var.pipeline_vm_machine_type
  zone         = var.zone
  labels       = local.labels
  tags         = ["ssh-access"]

  boot_disk {
    initialize_params {
      image = var.pipeline_vm_disk_image
      size  = var.pipeline_vm_disk_gb
    }
  }

  network_interface {
    network = var.vpc_network
    access_config {} # ephemeral public IP for SSH
  }

  service_account {
    email  = google_service_account.cloud_run.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = <<-EOF
    #!/bin/bash
    apt-get update && apt-get install -y docker.io docker-compose-plugin git
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ${var.ci_ssh_user} 2>/dev/null || true
  EOF

  depends_on = [google_project_service.apis]
}

resource "google_compute_firewall" "allow_ssh" {
  name    = "${local.prefix}-allow-ssh"
  network = var.vpc_network

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.ssh_source_ranges
  target_tags   = ["ssh-access"]
}

# Airflow apiserver on pipeline VM (port 8080).
resource "google_compute_firewall" "allow_airflow_ui" {
  name    = "${local.prefix}-allow-airflow-ui"
  network = var.vpc_network

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }

  source_ranges = var.airflow_ui_source_ranges
  target_tags   = ["ssh-access"]
}

# ---- Cloud Run: API ----
module "api" {
  source                = "../../modules/cloud_run"
  service_name          = local.api_service_name
  region                = var.region
  image                 = var.api_image
  port                  = 8080
  service_account_email = google_service_account.cloud_run.email
  cloud_sql_connection  = module.database.connection_name
  public_access         = var.api_public_access
  min_instances         = var.api_min_instances
  max_instances         = var.api_max_instances
  cpu                   = var.api_cpu
  memory                = var.api_memory
  labels                = local.labels

  env_vars = {
    ENVIRONMENT                    = var.environment
    DB_ENV                         = var.environment
    DB_USER                        = module.database.user_name
    DB_NAME                        = module.database.database_name
    DB_HOST                        = "/cloudsql/${module.database.connection_name}"
    INSTANCE_CONNECTION_NAME       = module.database.connection_name
    MLFLOW_TRACKING_URI            = module.mlflow.service_url
    VERTEX_PROJECT                 = var.project_id
    VERTEX_LOCATION                = var.region
    METRICS_ENABLED                = "true"
    GRAFANA_CLOUD_REMOTE_WRITE_URL = var.grafana_remote_write_url
    GRAFANA_CLOUD_USERNAME         = var.grafana_cloud_username
  }
  secret_env_vars = {
    DB_PASS               = { secret_id = module.db_password_secret.secret_id, version = "latest" }
    GRAFANA_CLOUD_API_KEY = { secret_id = module.grafana_api_key_secret.secret_id, version = "latest" }
  }

  depends_on = [google_project_service.apis, module.db_password_secret, module.grafana_api_key_secret, module.mlflow]
}

# ---- Cloud Run: Frontend ----
module "frontend" {
  source                = "../../modules/cloud_run"
  service_name          = local.frontend_service_name
  region                = var.region
  image                 = var.frontend_image
  port                  = var.frontend_port
  service_account_email = google_service_account.cloud_run.email
  cloud_sql_connection  = ""
  public_access         = true
  min_instances         = var.frontend_min_instances
  max_instances         = var.frontend_max_instances
  cpu                   = var.frontend_cpu
  memory                = var.frontend_memory
  labels                = local.labels

  env_vars = {
    ENVIRONMENT = var.environment
    API_URL     = module.api.service_url
  }

  depends_on = [google_project_service.apis, module.api]
}

# ---- Cloud Run: MLflow ----
module "mlflow" {
  source                = "../../modules/cloud_run"
  service_name          = local.mlflow_service_name
  region                = var.region
  image                 = var.mlflow_image
  port                  = 5000
  service_account_email = google_service_account.cloud_run.email
  cloud_sql_connection  = module.database.connection_name
  public_access         = var.mlflow_public_access
  min_instances         = var.mlflow_min_instances
  max_instances         = var.mlflow_max_instances
  cpu                   = var.mlflow_cpu
  memory                = var.mlflow_memory
  labels                = local.labels

  env_vars = {
    ENVIRONMENT              = var.environment
    DB_USER                  = module.database.user_name
    DB_NAME                  = module.database.database_name
    DB_HOST                  = "/cloudsql/${module.database.connection_name}"
    INSTANCE_CONNECTION_NAME = module.database.connection_name
    MLFLOW_ARTIFACT_ROOT     = "gs://${module.mlflow_bucket.bucket_name}/artifacts"
  }
  secret_env_vars = {
    DB_PASS = { secret_id = module.db_password_secret.secret_id, version = "latest" }
  }

  depends_on = [google_project_service.apis, module.db_password_secret]
}

# ---- Service-to-service IAM ----
resource "google_cloud_run_v2_service_iam_member" "frontend_invokes_api" {
  name     = module.api.service_name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_cloud_run_v2_service_iam_member" "api_invokes_mlflow" {
  name     = module.mlflow.service_name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.cloud_run.email}"
}
