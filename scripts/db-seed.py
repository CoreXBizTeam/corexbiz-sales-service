#!/usr/bin/env python3
"""Apply sales-service Postgres migrations (mirrors corex-share-service scripts/db-seed.js)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
_cloud = os.getenv("CLOUD_SQL_ENV", "/Users/tobymalek/corexbiz/cloud-sql/env.local")
if os.path.isfile(_cloud):
    load_dotenv(_cloud, override=False)

from src.db.migrate import run_migrations  # noqa: E402


def main() -> None:
    result = run_migrations()
    applied = result.get("applied") or []
    skipped = result.get("skipped") or []
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print(f"Schema up to date ({len(skipped)} migration(s) skipped).")


if __name__ == "__main__":
    main()
