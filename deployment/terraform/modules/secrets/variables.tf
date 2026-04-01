variable "secret_id" {
  description = "Secret Manager secret ID"
  type        = string
}

variable "secret_data" {
  description = "The secret value to store"
  type        = string
  sensitive   = true
}

variable "accessor_service_account_email" {
  description = "Service account email that needs read access"
  type        = string
}
