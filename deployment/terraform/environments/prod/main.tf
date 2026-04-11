terraform {
  required_version = ">= 1.14.7"
  required_providers {
    google = { source = "hashicorp/google"
      version = "~> 7.25.0" }
    random = { source = "hashicorp/random"
      version = "~> 3.8.1" }
    tls = { source = "hashicorp/tls"
      version = "~> 4.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {}

locals {
  prefix = "savvio-${var.environment}"
  labels = {
    project     = "savvio"
    environment = var.environment
    managed_by  = "terraform"
  }
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
  ])
  service            = each.key
  disable_on_destroy = false
}

# ---- Service Account ----
resource "google_service_account" "cloud_run" {
  account_id   = "${local.prefix}-run-sa"
  display_name = "SavVio ${var.environment} Cloud Run SA"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "run_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
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
  deletion_protection = true   # PROD: protect
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

# ---- Storage ----
module "dvc_bucket" {
  source        = "../../modules/storage"
  bucket_name   = "${local.prefix}-dvc-data"
  location      = var.region
  force_destroy = false   # PROD: protect data
  labels        = local.labels
}

module "mlflow_bucket" {
  source        = "../../modules/storage"
  bucket_name   = "${local.prefix}-mlflow-artifacts"
  location      = var.region
  force_destroy = false
  labels        = local.labels
}

# ---- SSH Key for CI/CD DAG Deploy ----
resource "tls_private_key" "deploy_ssh" {
  algorithm = "ED25519"
}

module "ssh_key_secret" {
  source                         = "../../modules/secrets"
  secret_id                      = "${local.prefix}-deploy-ssh-key"
  secret_data                    = tls_private_key.deploy_ssh.private_key_openssh
  accessor_service_account_email = google_service_account.cloud_run.email
  depends_on                     = [google_project_service.apis]
}

# ---- Artifact Registry ----
module "docker_repo" {
  source        = "../../modules/artifact_registry"
  repository_id = "${local.prefix}-docker-repo"
  location      = var.region
  labels        = local.labels
  depends_on    = [google_project_service.apis]
}

# ---- Cloud Run Job: ML Training ----
resource "google_cloud_run_v2_job" "training" {
  name     = "${local.prefix}-training"
  location = var.region

  template {
    task_count = 1

    template {
      service_account = google_service_account.cloud_run.email
      timeout         = "7200s"   # 2 hours for prod training

      containers {
        image = var.training_image

        resources {
          limits = {
            cpu    = "4"
            memory = "8Gi"
          }
        }

        env {
          name  = "ENVIRONMENT"
          value = var.environment
        }
        env {
          name  = "DB_USER"
          value = module.database.user_name
        }
        env {
          name  = "DB_NAME"
          value = module.database.database_name
        }
        env {
          name  = "INSTANCE_CONNECTION_NAME"
          value = module.database.connection_name
        }
        env {
          name  = "MLFLOW_TRACKING_URI"
          value = module.mlflow.service_url
        }
        env {
          name  = "MLFLOW_ARTIFACT_ROOT"
          value = "gs://${module.mlflow_bucket.bucket_name}/artifacts"
        }
        env {
          name = "DB_PASS"
          value_source {
            secret_key_ref {
              secret  = module.db_password_secret.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  labels     = local.labels
  depends_on = [google_project_service.apis, module.db_password_secret, module.mlflow]
}

# ---- GCE VM: Airflow Pipeline ----
resource "google_compute_instance" "pipeline_vm" {
  name         = "${local.prefix}-pipeline-vm"
  machine_type = "e2-standard-2"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 50
    }
  }

  network_interface {
    network = "default"
    access_config {}
  }

  metadata = {
    ssh-keys = "savvio:${tls_private_key.deploy_ssh.public_key_openssh}"
  }

  service_account {
    email  = google_service_account.cloud_run.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = <<-SCRIPT
    #!/bin/bash
    set -e

    # Install dependencies
    apt-get update && apt-get install -y docker.io docker-compose git
    systemctl enable docker && systemctl start docker

    # Clone repo (skip if already cloned)
    if [ ! -d /opt/savvio/.git ]; then
      mkdir -p /opt/savvio
      git clone https://github.com/nirajmehta960/SavVio.git /opt/savvio
    fi

    # Fetch DB password from Secret Manager
    DB_PASSWORD=$(gcloud secrets versions access latest \
      --secret="${module.db_password_secret.secret_id}" \
      --project="${var.project_id}" 2>/dev/null || echo "")

    # Get Cloud SQL public IP
    DB_HOST=$(gcloud sql instances describe ${module.database.instance_name} \
      --project="${var.project_id}" \
      --format='value(ipAddresses[0].ipAddress)' 2>/dev/null || echo "")

    # Write .env for Airflow docker-compose
    cat > /opt/savvio/data_pipeline/.env <<EOF
    DB_HOST=$DB_HOST
    DB_PORT=5432
    DB_NAME=${module.database.database_name}
    DB_USER=${module.database.user_name}
    DB_PASSWORD=$DB_PASSWORD
    AIRFLOW_UID=50000
    EOF

    # Start Airflow
    cd /opt/savvio/data_pipeline
    docker-compose up -d
  SCRIPT

  labels     = local.labels
  tags       = ["savvio-ssh"]
  depends_on = [google_project_service.apis, module.database, module.db_password_secret]
}

resource "google_compute_firewall" "ssh" {
  name    = "${local.prefix}-allow-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["savvio-ssh"]
}

# ---- Cloud Run: API ----
module "api" {
  source                = "../../modules/cloud_run"
  service_name          = "${local.prefix}-api"
  region                = var.region
  image                 = var.api_image
  port                  = 8080
  service_account_email = google_service_account.cloud_run.email
  cloud_sql_connection  = module.database.connection_name
  public_access         = false   # PROD: require IAM auth
  min_instances         = 1       # PROD: no cold starts
  max_instances         = 5
  cpu                   = "2"
  memory                = "2Gi"
  labels                = local.labels

  env_vars = {
    ENVIRONMENT              = var.environment
    DB_USER                  = module.database.user_name
    DB_NAME                  = module.database.database_name
    INSTANCE_CONNECTION_NAME = module.database.connection_name
    MLFLOW_TRACKING_URI      = module.mlflow.service_url
  }
  secret_env_vars = {
    DB_PASS = { secret_id = module.db_password_secret.secret_id, version = "latest" }
  }

  depends_on = [google_project_service.apis, module.db_password_secret, module.mlflow]
}

# ---- Cloud Run: Frontend ----
module "frontend" {
  source                = "../../modules/cloud_run"
  service_name          = "${local.prefix}-frontend"
  region                = var.region
  image                 = var.frontend_image
  port                  = 8501
  service_account_email = google_service_account.cloud_run.email
  cloud_sql_connection  = ""
  public_access         = true    # PROD: user-facing
  min_instances         = 1
  max_instances         = 3
  cpu                   = "1"
  memory                = "512Mi"
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
  service_name          = "${local.prefix}-mlflow"
  region                = var.region
  image                 = var.mlflow_image
  port                  = 5000
  service_account_email = google_service_account.cloud_run.email
  cloud_sql_connection  = module.database.connection_name
  public_access         = false   # PROD: internal only
  min_instances         = 1
  max_instances         = 2
  cpu                   = "1"
  memory                = "1Gi"
  labels                = local.labels

  env_vars = {
    ENVIRONMENT              = var.environment
    DB_USER                  = module.database.user_name
    DB_NAME                  = module.database.database_name
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
