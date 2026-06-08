#!/usr/bin/env bash
# Deploy CoreX Sales Service to Google Cloud Run.
#
# The HTTP API runs lead pipelines in background threads on the same service instance.
#
# Default target is **dev**. Use --production / -p for production.
#
# Prerequisites:
#   gcloud CLI (logged in), Cloud Run + Cloud Build APIs enabled
#   Cloud SQL instance + DATABASE_URL or POSTGRES_* + CLOUD_SQL_CONNECTION_NAME
#   Optional: Secret Manager secrets (SECRET_* in .env)
#
# Usage:
#   ./deploy.sh              # dev service
#   ./deploy.sh --production
#   SKIP_BUILD=1 ./deploy.sh --skip-build   # redeploy env/config only

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DEPLOY_TARGET="${DEPLOY_ENV:-dev}"
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
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --help | -h)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
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

# --- Cloud Run resource defaults (override via env) ---
SERVICE_PORT="${SERVICE_PORT:-8080}"
SERVICE_MEMORY="${SERVICE_MEMORY:-2Gi}"
SERVICE_CPU="${SERVICE_CPU:-2}"
SERVICE_CONCURRENCY="${SERVICE_CONCURRENCY:-80}"
SERVICE_TIMEOUT="${SERVICE_TIMEOUT:-3600}"
SERVICE_MIN_INSTANCES="${SERVICE_MIN_INSTANCES:-0}"
SERVICE_MAX_INSTANCES="${SERVICE_MAX_INSTANCES:-10}"
# Cloud Run throttles CPU after the HTTP response unless disabled. In-process
# workers need CPU after POST /runs returns 202 (BackgroundTasks + pool).
SERVICE_CPU_THROTTLING="${SERVICE_CPU_THROTTLING:-0}"

AUTH_FLAG=(--allow-unauthenticated)
if [[ "${CLOUD_RUN_REQUIRE_AUTH:-}" == "1" || "${CLOUD_RUN_REQUIRE_AUTH:-}" == "true" ]]; then
  AUTH_FLAG=(--no-allow-unauthenticated)
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: required command not found: $1" >&2
    exit 1
  }
}

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

# Write KEY=VALUE lines for gcloud --env-vars-file (handles special chars safely).
write_env_vars_file() {
  local dest="$1"
  shift
  python3 - "$dest" "$@" <<'PY'
import json, sys
from pathlib import Path

dest = sys.argv[1]
pairs = sys.argv[2:]
lines = []
for item in pairs:
    if "=" not in item:
        continue
    key, val = item.split("=", 1)
    lines.append(f"{key}: {json.dumps(val)}")
Path(dest).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
PY
}

require_cmd gcloud
require_cmd python3

if [[ ! -f "${ROOT}/Dockerfile" ]]; then
  echo "error: Dockerfile not found in ${ROOT}" >&2
  exit 1
fi

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [[ -n "${ACTIVE_PROJECT}" && "${ACTIVE_PROJECT}" != "${PROJECT_ID}" ]]; then
  echo "note: gcloud active project is ${ACTIVE_PROJECT}; deploying to ${PROJECT_ID}" >&2
fi

COMMON_ENV_PAIRS=()
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

# Never load a local .env file inside Cloud Run containers.
COMMON_ENV_PAIRS+=( "SALES_DISABLE_DOTENV=1" )
# PORT is reserved — set via `gcloud run deploy --port` and injected by Cloud Run.

append_env POSTGRES_SCHEMA
append_env DB_AUTO_SEED
append_env VALIDATE_SUBSCRIPTION_URL
append_env SUBSCRIPTION_VALIDATION_BYPASS
append_env LOG_LEVEL
append_env ADMIN_PASSWORD
append_env ADMIN_SESSION_SECRET

append_secret GOOGLE_MAPS_API_KEY SECRET_GOOGLE_MAPS_API_KEY
append_secret WEBHOOK_SIGNING_SECRET SECRET_WEBHOOK_SIGNING_SECRET
append_secret API_TOKEN SECRET_API_TOKEN

if [[ ${#SECRET_PAIRS[@]} -eq 0 ]]; then
  append_env GOOGLE_MAPS_API_KEY
  append_env WEBHOOK_SIGNING_SECRET
  append_env API_TOKEN
fi

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

GCLOUD_SQL=()
if [[ -n "${ENV_CLOUD_SQL_CONN}" ]]; then
  GCLOUD_SQL=( --set-cloudsql-instances="${ENV_CLOUD_SQL_CONN}" )
  echo "Cloud SQL instance: ${ENV_CLOUD_SQL_CONN}"
elif [[ "${ENV_DATABASE_URL}" == *"/cloudsql/"* ]]; then
  echo "warning: DATABASE_URL uses /cloudsql/ but CLOUD_SQL_CONNECTION_NAME is unset" >&2
fi

SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-}"
if [[ -z "${SERVICE_ACCOUNT}" ]]; then
  SERVICE_ACCOUNT="$(read_env_file_value CLOUD_RUN_SERVICE_ACCOUNT 2>/dev/null || true)"
fi
GCLOUD_SA=()
if [[ -n "${SERVICE_ACCOUNT}" ]]; then
  GCLOUD_SA=( --service-account="${SERVICE_ACCOUNT}" )
fi

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

ENV_FILE="$(mktemp)"
trap 'rm -f "${ENV_FILE}"' EXIT

write_env_vars_file "${ENV_FILE}" "${COMMON_ENV_PAIRS[@]}"

GCLOUD_SECRETS=()
if [[ ${#SECRET_PAIRS[@]} -gt 0 ]]; then
  _OLD_IFS="${IFS}"
  IFS=','
  SECRETS_JOINED="${SECRET_PAIRS[*]}"
  IFS="${_OLD_IFS}"
  GCLOUD_SECRETS=( --set-secrets="${SECRETS_JOINED}" )
fi

LABEL="${DEPLOY_TARGET}"
echo "Deploy target=${LABEL} project=${PROJECT_ID} region=${REGION}"
echo "  image=${IMAGE}"
echo "  service=${SERVICE_NAME}"

if [[ "${SKIP_BUILD}" -eq 0 ]]; then
  echo "Building container image..."
  gcloud builds submit "${ROOT}" \
    --project "${PROJECT_ID}" \
    --tag "${IMAGE}" \
    --quiet
else
  echo "Skipping image build (SKIP_BUILD=1)."
fi

echo "Deploying Cloud Run service ${SERVICE_NAME}..."
SERVICE_DEPLOY_ARGS=(
  gcloud run deploy "${SERVICE_NAME}"
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --platform managed
  --image "${IMAGE}"
  --port "${SERVICE_PORT}"
  --memory "${SERVICE_MEMORY}"
  --cpu "${SERVICE_CPU}"
  --concurrency "${SERVICE_CONCURRENCY}"
  --timeout "${SERVICE_TIMEOUT}"
  --min-instances "${SERVICE_MIN_INSTANCES}"
  --max-instances "${SERVICE_MAX_INSTANCES}"
  --startup-probe="httpGet.path=/health,httpGet.port=${SERVICE_PORT},initialDelaySeconds=5,timeoutSeconds=5,periodSeconds=10,failureThreshold=3"
  --liveness-probe="httpGet.path=/health,httpGet.port=${SERVICE_PORT},initialDelaySeconds=10,timeoutSeconds=5,periodSeconds=30,failureThreshold=3"
  --labels="env=${LABEL},component=corex-sales-service"
  --quiet
)
if [[ ${#AUTH_FLAG[@]} -gt 0 ]]; then
  SERVICE_DEPLOY_ARGS+=( "${AUTH_FLAG[@]}" )
fi
if [[ ${#GCLOUD_SQL[@]} -gt 0 ]]; then
  SERVICE_DEPLOY_ARGS+=( "${GCLOUD_SQL[@]}" )
fi
if [[ ${#GCLOUD_SA[@]} -gt 0 ]]; then
  SERVICE_DEPLOY_ARGS+=( "${GCLOUD_SA[@]}" )
fi
SERVICE_DEPLOY_ARGS+=( --env-vars-file="${ENV_FILE}" )
if [[ ${#GCLOUD_SECRETS[@]} -gt 0 ]]; then
  SERVICE_DEPLOY_ARGS+=( "${GCLOUD_SECRETS[@]}" )
fi
if [[ "${SERVICE_CPU_THROTTLING}" == "0" || "${SERVICE_CPU_THROTTLING}" == "false" ]]; then
  SERVICE_DEPLOY_ARGS+=( --no-cpu-throttling )
  echo "  cpu-throttling: disabled (required for in-process run workers after 202)"
fi
"${SERVICE_DEPLOY_ARGS[@]}"

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format='value(status.url)' 2>/dev/null || true)"
if [[ -n "${SERVICE_URL}" ]]; then
  echo "Service URL: ${SERVICE_URL}"
  echo "Health:      ${SERVICE_URL}/health"
fi

echo "Done."
if [[ -n "${SERVICE_URL:-}" ]]; then
  echo ""
  echo "=== Stack: local WP + Cloud share + Cloud sales ==="
  echo "  Lead-run webhooks: Cloud sales POSTs to webhook_url on each run (WP tunnel URL from the plugin)."
  echo "  Do NOT set SALES_SITE_URL on Cloud Run — it is for local sales-service dev only."
  echo "  Local WP: run corexbiz-core/scripts/cloudflared-dev-tunnel.sh and keep it up during runs."
  echo "  Share-service: deploy with ./deploy.sh; PLATFORM_SELF_URL is set to the share Cloud Run URL."
  echo "  WEBHOOK_SIGNING_SECRET here must match the WordPress plugin bundled secret."
fi
