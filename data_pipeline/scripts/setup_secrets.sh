#!/usr/bin/env bash
# Populate GCP Secret Manager with all secrets needed by the data pipeline.
#
# Usage:
#   bash data_pipeline/scripts/setup_secrets.sh <GCP_PROJECT_ID> [SECRET_PREFIX]
#
# Arguments:
#   GCP_PROJECT_ID  (required)  Target GCP project ID.
#   SECRET_PREFIX   (optional)  Prefix for all secret names. Defaults to "savvio".
#                               Must match the `SECRET_PREFIX` GitHub variable
#                               (or the workflow's default) so CI fetches the
#                               right names, e.g. "${PREFIX}-db-password".
#
# Prerequisites:
#   gcloud auth login
#   gcloud config set project <GCP_PROJECT_ID>
#   gcloud services enable secretmanager.googleapis.com
#
# After running, grant the Airflow VM's instance service account read access:
#   gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> \
#     --member='serviceAccount:<VM_SA_EMAIL>' \
#     --role='roles/secretmanager.secretAccessor'
#
# Then remove any of these values that still live in GitHub Actions secrets.
set -euo pipefail

PROJECT="${1:?Usage: $0 <GCP_PROJECT_ID> [SECRET_PREFIX]}"
PREFIX="${2:-savvio}"

upsert_secret() {
  local key="$1"
  local prompt="$2"
  local name="${PREFIX}-${key}"

  printf "%s: " "$prompt"
  read -rs value
  echo

  if gcloud secrets describe "$name" --project="$PROJECT" >/dev/null 2>&1; then
    printf "  Updating %s...\n" "$name"
    printf '%s' "$value" | gcloud secrets versions add "$name" \
      --project="$PROJECT" --data-file=-
  else
    printf "  Creating %s...\n" "$name"
    printf '%s' "$value" | gcloud secrets create "$name" \
      --project="$PROJECT" \
      --data-file=- \
      --replication-policy=automatic
  fi
}

echo "=== SavVio Data Pipeline — Secret Manager Setup ==="
echo "Project: $PROJECT"
echo "Prefix:  $PREFIX"
echo

upsert_secret "db-user"              "DB_USER"
upsert_secret "db-password"          "DB_PASSWORD"
upsert_secret "db-host"              "DB_HOST"
upsert_secret "db-port"              "DB_PORT (e.g. 5432)"
upsert_secret "db-name"              "DB_NAME"
upsert_secret "smtp-host"            "SMTP_HOST"
upsert_secret "smtp-port"            "SMTP_PORT (e.g. 587)"
upsert_secret "smtp-user"            "SMTP_USER"
upsert_secret "smtp-password"        "SMTP_PASSWORD"
upsert_secret "alert-email-from"     "ALERT_EMAIL_FROM"
upsert_secret "alert-email-list"     "ALERT_EMAIL_LIST"
upsert_secret "airflow-www-password" "AIRFLOW_WWW_PASSWORD"

echo
echo "=== Done ==="
echo "Next: grant the VM service account access with:"
echo "  gcloud projects add-iam-policy-binding $PROJECT \\"
echo "    --member='serviceAccount:<VM_SA_EMAIL>' \\"
echo "    --role='roles/secretmanager.secretAccessor'"
echo
echo "If you used a non-default prefix, also set the GitHub repo variable:"
echo "  SECRET_PREFIX=$PREFIX"
