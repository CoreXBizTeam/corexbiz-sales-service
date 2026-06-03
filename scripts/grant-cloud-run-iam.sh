#!/usr/bin/env bash
# One-time IAM grants so the Cloud Run **service** can execute the pipeline **job**
# and connect to Cloud SQL.
#
# Usage:
#   ./scripts/grant-cloud-run-iam.sh [--dev | --production]
#
# Override defaults:
#   GCP_PROJECT_ID, GCP_REGION, CLOUD_RUN_SERVICE, CLOUD_RUN_JOB_NAME, SERVICE_ACCOUNT

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEPLOY_TARGET="${DEPLOY_ENV:-dev}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --production | -p) DEPLOY_TARGET="production"; shift ;;
    --dev) DEPLOY_TARGET="dev"; shift ;;
    -h | --help)
      echo "Usage: $(basename "$0") [--dev | --production]"
      exit 0
      ;;
    *) echo "error: unknown option: $1" >&2; exit 1 ;;
  esac
done

PROJECT_ID="${GCP_PROJECT_ID:-corexbiz}"
REGION="${GCP_REGION:-us-west1}"

if [[ "${DEPLOY_TARGET}" == "production" ]]; then
  SERVICE_NAME="${CLOUD_RUN_SERVICE:-${CLOUD_RUN_PRODUCTION_SERVICE:-corexbiz-sales-service}}"
  JOB_NAME="${CLOUD_RUN_JOB_NAME:-${CLOUD_RUN_JOB_PRODUCTION_NAME:-corexbiz-sales-pipeline-job}}"
else
  SERVICE_NAME="${CLOUD_RUN_SERVICE:-${CLOUD_RUN_DEV_SERVICE:-corex-sales-service-dev}}"
  JOB_NAME="${CLOUD_RUN_JOB_NAME:-${CLOUD_RUN_DEV_JOB_NAME:-corex-sales-pipeline-job-dev}}"
fi

if [[ -n "${SERVICE_ACCOUNT:-}" ]]; then
  SA="${SERVICE_ACCOUNT}"
else
  SA="$(gcloud run services describe "${SERVICE_NAME}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true)"
  if [[ -z "${SA}" ]]; then
    PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
    SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  fi
fi

echo "Project:  ${PROJECT_ID}"
echo "Region:   ${REGION}"
echo "Service:  ${SERVICE_NAME}"
echo "Job:      ${JOB_NAME}"
echo "Identity: ${SA}"
echo

echo "Granting roles/cloudsql.client..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA}" \
  --role="roles/cloudsql.client" \
  --condition=None \
  --quiet >/dev/null

echo "Granting roles/run.developer (execute Cloud Run Jobs)..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA}" \
  --role="roles/run.developer" \
  --condition=None \
  --quiet >/dev/null

echo "Done. Re-deploy if the service account was just created."
