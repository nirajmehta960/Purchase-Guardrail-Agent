#!/bin/bash
set -e

PROJECT="savvio-ai"
REGION="us-east1"
ENV="dev"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/savvio-${ENV}-docker-repo"

echo "=== 0. Auth Docker ==="
# gcloud auth configure-docker ${REGION}-docker.pkg.dev --quiet

echo "=== 1. Create registry first (solves chicken-and-egg) ==="
cd terraform/environments/${ENV}
terraform apply -auto-approve -target=module.docker_repo
cd ../../..

echo "=== 2. Build & push test-app ==="
docker buildx build --platform linux/amd64,linux/arm64 --push -t ${REPO}/test-app:latest docker/test-app/
docker push ${REPO}/test-app:latest

echo "=== 3. Build & push mlflow ==="
docker buildx build --platform linux/amd64,linux/arm64 --push -t ${REPO}/mlflow:latest docker/mlflow/
docker push ${REPO}/mlflow:latest

echo "=== 4. Deploy ==="
cd terraform/environments/${ENV}
terraform apply -auto-approve \
  -var="api_image=${REPO}/test-app:latest" \
  -var="frontend_image=${REPO}/test-app:latest" \
  -var="mlflow_image=${REPO}/mlflow:latest"

echo "=== 5. Wait 30s for rollout ==="
sleep 30

echo "=== 6. Health checks ==="
API_URL=$(terraform output -raw api_url)
FRONTEND_URL=$(terraform output -raw frontend_url)
MLFLOW_URL=$(terraform output -raw mlflow_url)

for NAME_URL in "API|${API_URL}" "Frontend|${FRONTEND_URL}" "MLflow|${MLFLOW_URL}"; do
  NAME="${NAME_URL%%|*}"
  URL="${NAME_URL##*|}"
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -L --max-time 15 "${URL}" || echo "000")
  if [ "$CODE" = "200" ] || [ "$CODE" = "302" ]; then
    echo "  ✅ ${NAME}: ${CODE} — ${URL}"
  else
    echo "  ❌ ${NAME}: ${CODE} — ${URL}"
  fi
done

echo "=== Done ==="
