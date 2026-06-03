#!/usr/bin/env bash
# Shared env loader for local scripts. Source from repo scripts — do not execute directly.
#
# Precedence (last wins):
#   1. cloud-sql/env.local  — shared Postgres proxy defaults (optional fallback)
#   2. .env                 — project-local config (primary for corex-sales-python)
#   3. caller exports       — already-set shell vars are never overwritten
#
# Uses python-dotenv (not bash source) so passwords with $, >, etc. stay intact.

load_sales_env() {
  local root="${1:?root dir required}"
  local cloud_sql_env="${CLOUD_SQL_ENV:-/Users/tobymalek/corexbiz/cloud-sql/env.local}"
  local python="${root}/.venv/bin/python"
  if [ ! -x "$python" ]; then
    python="$(command -v python3 || command -v python || true)"
  fi
  if [ -z "$python" ]; then
    echo "error: python not found (need .venv or python3 for .env loading)" >&2
    return 1
  fi

  # shellcheck disable=SC2046
  eval "$(
    CLOUD_SQL_ENV_FILE="$cloud_sql_env" PROJECT_ENV_FILE="$root/.env" "$python" - <<'PY'
import os
import shlex
import sys

try:
    from dotenv import dotenv_values
except ImportError:
    print("error: python-dotenv not installed", file=sys.stderr)
    sys.exit(1)

merged: dict[str, str] = {}
for path in (os.environ.get("CLOUD_SQL_ENV_FILE", ""), os.environ.get("PROJECT_ENV_FILE", "")):
    if not path or not os.path.isfile(path):
        continue
    for key, val in dotenv_values(path).items():
        if val is not None and str(val).strip() != "":
            merged[key] = str(val)

for key, val in merged.items():
    print(f"export {key}={shlex.quote(val)}")
PY
  )"
}

apply_sales_env_defaults() {
  local root
  root="$(cd "${1:-${ROOT:-.}}" && pwd)"
  export ROOT="$root"

  # Auto-load .env when apply_sales_env_defaults is called without a prior load_sales_env.
  if [ -f "${root}/.env" ] && [ -z "${GOOGLE_MAPS_API_KEY:-}" ]; then
    load_sales_env "$root"
  fi

  export COREX_SALES_SERVICE_ENV="${COREX_SALES_SERVICE_ENV:-local}"
  export POSTGRES_HOST="${POSTGRES_HOST:-${CLOUD_SQL_PROXY_HOST:-127.0.0.1}}"
  export POSTGRES_PORT="${POSTGRES_PORT:-${CLOUD_SQL_PROXY_PORT:-5432}}"
  export POSTGRES_DB="${POSTGRES_DB:-corexbiz-db}"
  export POSTGRES_USER="${POSTGRES_USER:-postgres}"
  export POSTGRES_SSLMODE="${POSTGRES_SSLMODE:-disable}"
  export POSTGRES_SCHEMA="${POSTGRES_SCHEMA:-sales-service}"
  export PORT="${PORT:-8080}"
  export HOST="${HOST:-127.0.0.1}"
  export SALES_WORKER_MODE="${SALES_WORKER_MODE:-inline}"
  export API_TOKEN="${API_TOKEN:-dev-sales-service-token}"
  export WEBHOOK_SIGNING_SECRET="${WEBHOOK_SIGNING_SECRET:-dev-sales-webhook-secret}"
  export DB_AUTO_SEED="${DB_AUTO_SEED:-true}"

  if [ -z "${GOOGLE_MAPS_API_KEY:-}" ]; then
    echo "warning: GOOGLE_MAPS_API_KEY not set — Google Maps lead runs will fail" >&2
    echo "  Add your key to ${root}/.env (Places API must be enabled in Google Cloud)" >&2
    echo "  Tip: run load_sales_env \"${root}\" after sourcing scripts/load-env.sh" >&2
  fi

  if [ -z "${DATABASE_URL:-}" ] && [ -n "${POSTGRES_PASSWORD:-}" ]; then
    export DATABASE_URL="$(
      ROOT_DIR="${ROOT:-.}" POSTGRES_HOST="$POSTGRES_HOST" POSTGRES_PORT="$POSTGRES_PORT" \
        POSTGRES_DB="$POSTGRES_DB" POSTGRES_USER="$POSTGRES_USER" \
        POSTGRES_PASSWORD="$POSTGRES_PASSWORD" POSTGRES_SSLMODE="$POSTGRES_SSLMODE" \
        "${ROOT:-.}/.venv/bin/python" - <<'PY'
import os
import sys
from pathlib import Path

root = Path(os.environ.get("ROOT_DIR", ".")).resolve()
sys.path.insert(0, str(root))
from src.db.connection import resolve_database_url

url = resolve_database_url()
print(url or "", end="")
PY
    )"
  fi
}

warn_if_postgres_missing() {
  if [ -z "${DATABASE_URL:-}" ] && [ -z "${POSTGRES_PASSWORD:-}" ]; then
    echo "warning: DATABASE_URL / POSTGRES_PASSWORD not set — HTTP /health only" >&2
    echo "  Copy .env.example to .env and set POSTGRES_PASSWORD" >&2
    echo "  Start Cloud SQL proxy: /Users/tobymalek/corexbiz/cloud-sql/start-proxy.sh" >&2
  fi
}
