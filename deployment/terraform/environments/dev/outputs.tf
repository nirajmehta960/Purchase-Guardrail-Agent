output "api_url"               { value = module.api.service_url }
output "frontend_url"          { value = module.frontend.service_url }
output "mlflow_url"            { value = module.mlflow.service_url }
output "db_connection_name"    { value = module.database.connection_name }
output "dvc_bucket"            { value = module.dvc_bucket.bucket_name }
output "mlflow_artifact_bucket" { value = module.mlflow_bucket.bucket_name }
output "docker_repo_url"       { value = module.docker_repo.repository_url }
