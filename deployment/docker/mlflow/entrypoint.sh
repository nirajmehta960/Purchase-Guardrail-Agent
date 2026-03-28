#!/bin/bash
set -e

# Cloud SQL proxy mounts a Unix socket at /cloudsql/<connection_name>
BACKEND_URI="postgresql://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=/cloudsql/${INSTANCE_CONNECTION_NAME}"

exec mlflow server \
  --backend-store-uri "${BACKEND_URI}" \
  --default-artifact-root "${MLFLOW_ARTIFACT_ROOT}" \
  --host 0.0.0.0 \
  --port 5000
