#!/usr/bin/env bash
# Deploy CoreX Sales Service + pipeline worker job to Google Cloud Run.
#
# Builds one container image, deploys the HTTP API service, and deploys a Cloud Run Job
# that runs `python -m src.worker.run_job` with SALES_RUN_SPEC set per execution.
#
# Default target is **dev**. Use --production / -p for production.
#
# Requires: gcloud CLI, Cloud Run + Cloud Build (+ optional Secret Manager) APIs enabled.
#
# Optional environment overrides:
#   GCP_PROJECT_ID                  default: corexbiz
#   GCP_REGION                      default: us-west1
#   DEPLOY_ENV                      dev | production (default: dev)
#   CLOUD_RUN_SERVICE               override HTTP service name
#   CLOUD_RUN_DEV_SERVICE           default: corex-sales-service-dev
#   CLOUD_RUN_PRODUCTION_SERVICE    default: corexbiz-sales-service
#   CLOUD_RUN_JOB_NAME              override pipeline job name
#   CLOUD_RUN_DEV_JOB_NAME          default: corex-sales-pipeline-job-dev
#   CLOUD_RUN_PRODUCTION_JOB_NAME   default: corexbiz-sales-pipeline-job
#   CONTAINER_IMAGE                 skip build and use this image URI
#   IMAGE_TAG                       tag for gcr.io build (default: git short SHA)
#   SKIP_BUILD=1                    deploy existing CONTAINER_IMAGE / latest tag
#   DATABASE_URL / CLOUD_RUN_DATABASE_URL / POSTGRES_* / CLOUD_SQL_CONNECTION_NAME
#   GOOGLE_MAPS_API_KEY, WEBHOOK_SIGNING_SECRET, API_TOKEN
#   SECRET_GOOGLE_MAPS_API_KEY      Secret Manager name:latest (optional)
#   SECRET_WEBHOOK_SIGNING_SECRET   Secret Manager name:latest (optional)
#   SECRET_API_TOKEN                Secret Manager name:latest (optional)
#   SALES_WORKER_MODE               default: job on Cloud Run
#   CLOUD_RUN_REQUIRE_AUTH          set to 1 to require IAM (default: public invoke)
#   JOB_MEMORY                      default: 2Gi
#   JOB_CPU                         default: 2
#   JOB_TASK_TIMEOUT                default: 3600 (seconds)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DEPLOY_TARGET="${DEPLOY_ENV:-dev}"
DEPLOY_SERVICE=1
DEPLOY_JOB=1
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --production | -p)
      DEPLOY_TARGET="production"
      shift
      ;;
    --dev)
      DEPLOY_TARGET="dev"
      shift
      ;;
    --service-only)
      DEPLOY_JOB=0
      shift
      ;;
    --job-only)
      DEPLOY_SERVICE=0
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --help | -h)
      cat <<EOF
Usage: $(basename "$0") [options]

Build one image and deploy the CoreX Sales API service + pipeline Cloud Run Job.

Options:
  --dev                 Deploy dev targets (default)
  --production, -p      Deploy production targets
  --service-only        Update HTTP service only
  --job-only            Update pipeline job only
  --skip-build          Use CONTAINER_IMAGE or existing tag (set SKIP_BUILD=1)
  -h, --help            Show this help

After first deploy, run ./scripts/grant-cloud-run-iam.sh so the service can execute the job.
EOF
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "${SKIP_BUILD:-}" == "1" ]]; then
  SKIP_BUILD=1
fi

case "${DEPLOY_TARGET}" in
  dev | development) DEPLOY_TARGET="dev" ;;
  production | prod) DEPLOY_TARGET="production" ;;
  *)
    echo "error: DEPLOY_ENV must be dev or production (got: ${DEPLOY_TARGET})" >&2
    exit 1
    ;;
esac

PROJECT_ID="${GCP_PROJECT_ID:-corexbiz}"
REGION="${GCP_REGION:-us-west1}"
IMAGE_REPO="${CONTAINER_IMAGE_REPO:-gcr.io/${PROJECT_ID}}"
IMAGE_NAME="${CONTAINER_IMAGE_NAME:-corex-sales-service}"
TAG="${IMAGE_TAG:-$(git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
IMAGE="${CONTAINER_IMAGE:-${IMAGE_REPO}/${IMAGE_NAME}:${TAG}}"

if [[ -n "${CLOUD_RUN_SERVICE:-}" ]]; then
  SERVICE_NAME="${CLOUD_RUN_SERVICE}"
elif [[ "${DEPLOY_TARGET}" == "production" ]]; then
  SERVICE_NAME="${CLOUD_RUN_PRODUCTION_SERVICE:-corexbiz-sales-service}"
else
  SERVICE_NAME="${CLOUD_RUN_DEV_SERVICE:-corex-sales-service-dev}"
fi

if [[ -n "${CLOUD_RUN_JOB_NAME:-}" ]]; then
  JOB_NAME="${CLOUD_RUN_JOB_NAME}"
elif [[ "${DEPLOY_TARGET}" == "production" ]]; then
  JOB_NAME="${CLOUD_RUN_PRODUCTION_JOB_NAME:-corexbiz-sales-pipeline-job}"
else
  JOB_NAME="${CLOUD_RUN_DEV_JOB_NAME:-corex-sales-pipeline-job-dev}"
fi

AUTH_FLAG=(--allow-unauthenticated)
if [[ "${CLOUD_RUN_REQUIRE_AUTH:-}" == "1" || "${CLOUD_RUN_REQUIRE_AUTH:-}" == "true" ]]; then
  AUTH_FLAG=(--no-allow-unauthenticated)
fi

read_env_file_value() {
  local key="$1"
  local file="${ROOT}/.env"
  local line val
  [[ -f "$file" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}=" "$file" 2>/dev/null | tail -1)" || return 1
  val="${line#*=}"
  val="${val%$'\r'}"
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  case "$val" in
    \"*\") val="${val#\"}"; val="${val%\"}" ;;
    \'*\') val="${val#\'}"; val="${val%\'}" ;;
  esac
  printf '%s' "$val"
}

if [[ ! -f "${ROOT}/Dockerfile" ]]; then
  echo "error: Dockerfile not found in ${ROOT}" >&2
  exit 1
fi

COMMON_ENV_PAIRS=()
SERVICE_ONLY_ENV_PAIRS=()
SECRET_PAIRS=()

append_env() {
  local key="$1"
  local val="${!key-}"
  if [[ -z "${val}" ]]; then
    val="$(read_env_file_value "${key}" 2>/dev/null || true)"
  fi
  if [[ -n "${val}" ]]; then
    COMMON_ENV_PAIRS+=( "${key}=${val}" )
  fi
}

append_secret() {
  local env_key="$1"
  local secret_env_name="$2"
  local secret_ref="${!secret_env_name-}"
  if [[ -z "${secret_ref}" ]]; then
    secret_ref="$(read_env_file_value "${secret_env_name}" 2>/dev/null || true)"
  fi
  if [[ -n "${secret_ref}" ]]; then
    SECRET_PAIRS+=( "${env_key}=${secret_ref}" )
  fi
}

ENV_DATABASE_URL="${CLOUD_RUN_DATABASE_URL-}"
if [[ -z "${ENV_DATABASE_URL}" ]]; then
  ENV_DATABASE_URL="$(read_env_file_value CLOUD_RUN_DATABASE_URL 2>/dev/null || true)"
fi
if [[ -z "${ENV_DATABASE_URL}" ]]; then
  ENV_DATABASE_URL="${DATABASE_URL-}"
fi
if [[ -z "${ENV_DATABASE_URL}" ]]; then
  ENV_DATABASE_URL="$(read_env_file_value DATABASE_URL 2>/dev/null || true)"
fi
if [[ -z "${ENV_DATABASE_URL}" ]]; then
  PG_USER="$(read_env_file_value POSTGRES_USER 2>/dev/null || true)"
  PG_PASS="$(read_env_file_value POSTGRES_PASSWORD 2>/dev/null || true)"
  PG_DB="$(read_env_file_value POSTGRES_DB 2>/dev/null || true)"
  PG_CONN="$(read_env_file_value CLOUD_SQL_CONNECTION_NAME 2>/dev/null || true)"
  if [[ -n "${PG_USER}" && -n "${PG_PASS}" && -n "${PG_DB}" && -n "${PG_CONN}" ]]; then
    ENV_DATABASE_URL="$(
      PG_USER="${PG_USER}" PG_PASS="${PG_PASS}" PG_DB="${PG_DB}" PG_CONN="${PG_CONN}" python3 - <<'PY'
import os, urllib.parse
user = os.environ["PG_USER"]
password = os.environ["PG_PASS"]
db = os.environ["PG_DB"]
conn = os.environ["PG_CONN"]
print(
    "postgresql://{user}:{password}@/{db}?host=/cloudsql/{conn}".format(
        user=urllib.parse.quote(user, safe=""),
        password=urllib.parse.quote(password, safe=""),
        db=urllib.parse.quote(db, safe=""),
        conn=conn,
    )
)
PY
    )"
  fi
fi

if [[ -n "${ENV_DATABASE_URL}" ]]; then
  COMMON_ENV_PAIRS+=( "DATABASE_URL=${ENV_DATABASE_URL}" )
fi

append_env POSTGRES_SCHEMA
append_env DB_AUTO_SEED
append_env VALIDATE_SUBSCRIPTION_URL
append_env SUBSCRIPTION_VALIDATION_BYPASS

append_secret GOOGLE_MAPS_API_KEY SECRET_GOOGLE_MAPS_API_KEY
append_secret WEBHOOK_SIGNING_SECRET SECRET_WEBHOOK_SIGNING_SECRET
append_secret API_TOKEN SECRET_API_TOKEN

if [[ ${#SECRET_PAIRS[@]} -eq 0 ]]; then
  append_env GOOGLE_MAPS_API_KEY
  append_env WEBHOOK_SIGNING_SECRET
  append_env API_TOKEN
fi

ENV_WORKER_MODE="${SALES_WORKER_MODE-}"
if [[ -z "${ENV_WORKER_MODE}" ]]; then
  ENV_WORKER_MODE="$(read_env_file_value SALES_WORKER_MODE 2>/dev/null || true)"
fi
if [[ -z "${ENV_WORKER_MODE}" ]]; then
  ENV_WORKER_MODE="job"
fi
SERVICE_ONLY_ENV_PAIRS+=( "SALES_WORKER_MODE=${ENV_WORKER_MODE}" )
SERVICE_ONLY_ENV_PAIRS+=( "SALES_CLOUD_RUN_JOB=${JOB_NAME}" )
SERVICE_ONLY_ENV_PAIRS+=( "GCP_PROJECT_ID=${PROJECT_ID}" )
SERVICE_ONLY_ENV_PAIRS+=( "GCP_REGION=${REGION}" )

if [[ "${DEPLOY_TARGET}" == "production" ]]; then
  COMMON_ENV_PAIRS+=( "COREX_SALES_SERVICE_ENV=production" )
else
  COMMON_ENV_PAIRS+=( "COREX_SALES_SERVICE_ENV=dev" )
fi

ENV_CLOUD_SQL_CONN="${CLOUD_SQL_CONNECTION_NAME-}"
if [[ -z "${ENV_CLOUD_SQL_CONN}" ]]; then
  ENV_CLOUD_SQL_CONN="$(read_env_file_value CLOUD_SQL_CONNECTION_NAME 2>/dev/null || true)"
fi
if [[ -z "${ENV_CLOUD_SQL_CONN}" && "${ENV_DATABASE_URL}" == *"/cloudsql/"* ]]; then
  ENV_CLOUD_SQL_CONN="$(
    python3 -c 'import re,sys; m=re.search(r"host=/cloudsql/([^&?]+)", sys.argv[1]); print(m.group(1) if m else "", end="")' \
      "${ENV_DATABASE_URL}"
  )"
fi

join_env_pairs() {
  local -n _arr=$1
  local IFS=','
  echo "${_arr[*]}"
}

GCLOUD_SQL=()
if [[ -n "${ENV_CLOUD_SQL_CONN}" ]]; then
  GCLOUD_SQL=( --set-cloudsql-instances="${ENV_CLOUD_SQL_CONN}" )
  echo "Cloud SQL instance: ${ENV_CLOUD_SQL_CONN}"
elif [[ "${ENV_DATABASE_URL}" == *"/cloudsql/"* ]]; then
  echo "warning: DATABASE_URL uses /cloudsql/ but CLOUD_SQL_CONNECTION_NAME is unset" >&2
fi

GCLOUD_SECRETS=()
if [[ ${#SECRET_PAIRS[@]} -gt 0 ]]; then
  GCLOUD_SECRETS=( --set-secrets="$(join_env_pairs SECRET_PAIRS)" )
fi

JOB_MEMORY="${JOB_MEMORY:-2Gi}"
JOB_CPU="${JOB_CPU:-2}"
JOB_TASK_TIMEOUT="${JOB_TASK_TIMEOUT:-3600}"

SEED_DATABASE_URL="${DATABASE_URL-}"
if [[ -z "${SEED_DATABASE_URL}" ]]; then
  SEED_DATABASE_URL="$(read_env_file_value DATABASE_URL 2>/dev/null || true)"
fi

if [[ -n "${SEED_DATABASE_URL}" && "${SEED_DATABASE_URL}" != *"/cloudsql/"* ]]; then
  echo "Applying sales-service schema migrations before deploy..."
  if DATABASE_URL="${SEED_DATABASE_URL}" python3 "${ROOT}/scripts/db-seed.py"; then
    echo "Database schema up to date."
  else
    echo "warning: pre-deploy db seed failed (is Cloud SQL proxy running?). Container startup will retry." >&2
  fi
fi

LABEL="${DEPLOY_TARGET}"
echo "Deploy target=${LABEL} project=${PROJECT_ID} region=${REGION}"
echo "  image=${IMAGE}"
echo "  service=${SERVICE_NAME} (deploy=${DEPLOY_SERVICE})"
echo "  job=${JOB_NAME} (deploy=${DEPLOY_JOB})"
echo "  SALES_WORKER_MODE=${ENV_WORKER_MODE}"

if [[ "${SKIP_BUILD}" -eq 0 ]]; then
  echo "Building container image..."
  gcloud builds submit "${ROOT}" \
    --project "${PROJECT_ID}" \
    --tag "${IMAGE}" \
    --quiet
else
  echo "Skipping image build (SKIP_BUILD=1)."
fi

if [[ "${DEPLOY_SERVICE}" -eq 1 ]]; then
  SERVICE_ENV=( "${COMMON_ENV_PAIRS[@]}" "${SERVICE_ONLY_ENV_PAIRS[@]}" )
  SERVICE_ENV_JOINED="$(join_env_pairs SERVICE_ENV)"
  echo "Deploying Cloud Run service ${SERVICE_NAME}..."
  gcloud run deploy "${SERVICE_NAME}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --platform managed \
    --image "${IMAGE}" \
    --quiet \
    "${AUTH_FLAG[@]}" \
    "${GCLOUD_SQL[@]}" \
    --update-env-vars="${SERVICE_ENV_JOINED}" \
    "${GCLOUD_SECRETS[@]}"
fi

if [[ "${DEPLOY_JOB}" -eq 1 ]]; then
  JOB_ENV_JOINED="$(join_env_pairs COMMON_ENV_PAIRS)"
  echo "Deploying Cloud Run Job ${JOB_NAME}..."
  gcloud run jobs deploy "${JOB_NAME}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --image "${IMAGE}" \
    --command python \
    --args=-m,src.worker.run_job \
    --tasks 1 \
    --max-retries 1 \
    --task-timeout "${JOB_TASK_TIMEOUT}" \
    --memory "${JOB_MEMORY}" \
    --cpu "${JOB_CPU}" \
    --quiet \
    "${GCLOUD_SQL[@]}" \
    --update-env-vars="${JOB_ENV_JOINED}" \
    "${GCLOUD_SECRETS[@]}"
fi

echo "Done."
if [[ "${DEPLOY_SERVICE}" -eq 1 && "${ENV_WORKER_MODE}" == "job" ]]; then
  echo "Tip: run ./scripts/grant-cloud-run-iam.sh --${DEPLOY_TARGET} if job execution returns 403."
fi
