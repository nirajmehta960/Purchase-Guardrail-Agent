variable "project_id" { type = string }
variable "region" { type = string }
variable "zone" { type = string }
variable "environment" { type = string }
variable "db_tier" { type = string }
variable "api_image" { type = string }
variable "frontend_image" { type = string }
variable "mlflow_image" { type = string }

variable "name_prefix" {
  description = "Org/product prefix joined with environment as '<name_prefix>-<environment>' for resource names and labels"
  type        = string
  default     = "savvio"
}

variable "api_service_name" {
  description = "Cloud Run service name for the API (must equal vars.CLOUD_RUN_API in CI). Null falls back to '<prefix>-api'."
  type        = string
  default     = null
}

variable "frontend_service_name" {
  description = "Cloud Run service name for the frontend (must equal vars.CLOUD_RUN_FRONTEND). Null falls back to '<prefix>-frontend'."
  type        = string
  default     = null
}

variable "mlflow_service_name" {
  description = "Cloud Run service name for the MLflow tracking server (must equal vars.CLOUD_RUN_MLFLOW). Null falls back to '<prefix>-mlflow'."
  type        = string
  default     = null
}

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
  default = 100
}

variable "vpc_network" {
  type    = string
  default = "default"
}

variable "ssh_source_ranges" {
  description = "CIDRs allowed to SSH to the pipeline VM. Tighten for prod (e.g. office IP / IAP only)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "airflow_ui_source_ranges" {
  description = "CIDRs allowed to reach the Airflow UI on the pipeline VM."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ci_ssh_user" {
  description = "OS user provisioned on the pipeline VM for CI SSH access (must equal vars.SSH_USER)"
  type        = string
  default     = "github-actions"
}

# Cloud Run sizing — prod defaults: warmer + bigger.
variable "api_min_instances" {
  type    = number
  default = 1
}
variable "api_max_instances" {
  type    = number
  default = 5
}
variable "api_cpu" {
  type    = string
  default = "2"
}
variable "api_memory" {
  type    = string
  default = "2Gi"
}
variable "api_public_access" {
  type    = bool
  default = false
}

variable "frontend_min_instances" {
  type    = number
  default = 1
}
variable "frontend_max_instances" {
  type    = number
  default = 3
}
variable "frontend_cpu" {
  type    = string
  default = "1"
}
variable "frontend_memory" {
  type    = string
  default = "512Mi"
}
variable "frontend_port" {
  type    = number
  default = 8501
}

variable "mlflow_min_instances" {
  type    = number
  default = 1
}
variable "mlflow_max_instances" {
  type    = number
  default = 2
}
variable "mlflow_cpu" {
  type    = string
  default = "1"
}
variable "mlflow_memory" {
  type    = string
  default = "1Gi"
}
variable "mlflow_public_access" {
  type    = bool
  default = false
}

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
