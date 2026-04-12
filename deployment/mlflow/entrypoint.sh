#!/bin/bash
set -e

# Build PostgreSQL URI from Cloud Run env vars injected by Terraform.
# DB_HOST is the Unix socket path: /cloudsql/PROJECT:REGION:INSTANCE
BACKEND_URI="postgresql://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=${DB_HOST}"

exec mlflow server \
  --backend-store-uri "${BACKEND_URI}" \
  --artifacts-destination "${MLFLOW_ARTIFACT_ROOT}" \
  --host 0.0.0.0 \
  --port "${PORT:-5000}" \
  --serve-artifacts
