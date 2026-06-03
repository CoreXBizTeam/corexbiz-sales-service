#!/usr/bin/env bash
# Start sales-service locally. Configuration: .env (primary) + optional cloud-sql/env.local fallback.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export ROOT

# shellcheck disable=SC1091
source "$ROOT/scripts/load-env.sh"
load_sales_env "$ROOT"
apply_sales_env_defaults
warn_if_postgres_missing

if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "warning: port ${PORT} already in use — stop the other process or set PORT=8081 in .env" >&2
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true
  exit 1
fi

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "Creating .venv..."
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements-service.txt"
else
  "$ROOT/.venv/bin/pip" install -q -r "$ROOT/requirements-service.txt"
fi

echo "Config:        ${ROOT}/.env"
echo "COREX_SALES_SERVICE_ENV=${COREX_SALES_SERVICE_ENV}"
echo "Postgres:      ${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB} (schema ${POSTGRES_SCHEMA})"
echo "SALES_WORKER_MODE=${SALES_WORKER_MODE}"
echo "SALES_SITE_ID=${SALES_SITE_ID:-(unset)}"
echo "SALES_SITE_URL=${SALES_SITE_URL:-(unset)}"
echo "API_TOKEN:     set (${#API_TOKEN} chars)"
echo "Listening:     http://${HOST}:${PORT}"
echo "WP proxy:      COREXBIZ_SALES_SERVICE_BASE_URL should match http://${HOST}:${PORT}"
echo "               (Generate list → WP /sales/lead-runs → corex-sales-service /api/v1/runs)"
echo ""

exec "$ROOT/.venv/bin/uvicorn" src.api.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --reload
