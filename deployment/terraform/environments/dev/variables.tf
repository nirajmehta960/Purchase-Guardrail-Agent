variable "project_id" { type = string }
variable "region" { type = string }
variable "zone" { type = string }
variable "environment" { type = string }
variable "db_tier" { type = string }
variable "api_image" { type = string }
variable "frontend_image" { type = string }
variable "mlflow_image" { type = string }

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
variable "name_prefix" {
  description = "Org/product prefix joined with environment as '<name_prefix>-<environment>' for resource names and labels"
  type        = string
  default     = "savvio"
}

variable "api_service_name" {
  description = "Cloud Run service name for the API. MUST equal vars.CLOUD_RUN_API in GitHub Actions or deploys silently target a non-existent service. Set null to fall back to '<prefix>-api'."
  type        = string
  default     = null
}

variable "frontend_service_name" {
  description = "Cloud Run service name for the frontend (must equal vars.CLOUD_RUN_FRONTEND). Set null to fall back to '<prefix>-frontend'."
  type        = string
  default     = null
}

variable "mlflow_service_name" {
  description = "Cloud Run service name for the MLflow tracking server (must equal vars.CLOUD_RUN_MLFLOW). Set null to fall back to '<prefix>-mlflow'."
  type        = string
  default     = null
}

# ---------------------------------------------------------------------------
# External GCS bucket (managed outside Terraform, e.g. Airflow data lake)
# ---------------------------------------------------------------------------
variable "data_bucket_name" {
  description = "Name of an externally-managed GCS bucket the Cloud Run SA must read/write. Empty string skips the IAM grant."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Pipeline VM (GCE)
# ---------------------------------------------------------------------------
variable "pipeline_vm_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "pipeline_vm_disk_image" {
  type    = string
  default = "debian-cloud/debian-12"
}

variable "pipeline_vm_disk_gb" {
  type    = number
  default = 50
}

variable "vpc_network" {
  description = "VPC network for the pipeline VM and its firewall rules"
  type        = string
  default     = "default"
}

variable "ssh_source_ranges" {
  description = "CIDRs allowed to SSH (port 22) to the pipeline VM. Defaults to open; restrict for prod or rely on IAP."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "airflow_ui_source_ranges" {
  description = "CIDRs allowed to reach the Airflow UI (port 8080) on the pipeline VM."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "iap_source_range" {
  description = "Google IAP TCP forwarding source range. Constant unless Google publishes a new one."
  type        = string
  default     = "35.235.240.0/20"
}

variable "ci_ssh_user" {
  description = "OS user provisioned on the pipeline VM for CI SSH access (must equal vars.SSH_USER in GitHub Actions)"
  type        = string
  default     = "github-actions"
}

variable "ci_ssh_public_keys" {
  description = "List of SSH public keys installed in /home/<ci_ssh_user>/.ssh/authorized_keys. Pass via TF_VAR_ci_ssh_public_keys or the tfvars file. Must include the key whose private half is stored as GCE_SSH_PRIVATE_KEY in GitHub Secrets."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Cloud Run sizing — sensible defaults; override per-env in tfvars
# ---------------------------------------------------------------------------
variable "api_min_instances" {
  type    = number
  default = 1
}
variable "api_max_instances" {
  type    = number
  default = 2
}
variable "api_cpu" {
  type    = string
  default = "1"
}
variable "api_memory" {
  type    = string
  default = "1Gi"
}

variable "frontend_min_instances" {
  type    = number
  default = 0
}
variable "frontend_max_instances" {
  type    = number
  default = 2
}
variable "frontend_cpu" {
  type    = string
  default = "1"
}
variable "frontend_memory" {
  type    = string
  default = "512Mi"
}

variable "mlflow_min_instances" {
  type    = number
  default = 0
}
variable "mlflow_max_instances" {
  type    = number
  default = 1
}
variable "mlflow_cpu" {
  type    = string
  default = "1"
}
variable "mlflow_memory" {
  type    = string
  default = "1Gi"
}

# ---------------------------------------------------------------------------
# Grafana Cloud monitoring
# Pass via TF_VAR_* env vars or -var flags — never commit secrets to tfvars
# ---------------------------------------------------------------------------
variable "grafana_remote_write_url" {
  description = "Grafana Cloud Prometheus remote-write URL"
  type        = string
  default     = ""
}

variable "grafana_cloud_username" {
  description = "Grafana Cloud Prometheus instance numeric ID"
  type        = string
  default     = ""
}

variable "grafana_api_key" {
  description = "Grafana Cloud API key for remote-write authentication"
  type        = string
  sensitive   = true
  default     = ""
}

variable "smtp_password" {
  description = "SMTP password for Airflow email alerts (e.g. Gmail app password)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "airflow_www_password" {
  description = "Airflow webserver admin password"
  type        = string
  sensitive   = true
  default     = ""
}
