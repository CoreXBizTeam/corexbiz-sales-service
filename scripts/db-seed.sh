#!/usr/bin/env bash
# Apply sales-service Postgres migrations using the project venv.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT

# shellcheck disable=SC1091
source "$ROOT/scripts/load-env.sh"
load_sales_env "$ROOT"
apply_sales_env_defaults
warn_if_postgres_missing

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "Creating .venv..."
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements-service.txt"
fi

exec "$ROOT/.venv/bin/python" "$ROOT/scripts/db-seed.py"
