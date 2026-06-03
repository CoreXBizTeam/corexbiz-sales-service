#!/usr/bin/env python3
"""
DEPRECATED (Phase 8): static JSON export for legacy Vue fallback.

Production flow uses corex-sales-service Postgres + WordPress REST
(``/wp-json/corexbiz/v1/sales/leads-bundle``). This script remains for
offline snapshots / debugging only.

Usage (legacy):
  python export_qualified_leads_json.py -o ../corexbiz-core/assets/dev/leads.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import db as dbmod

DEFAULT_DB = Path(__file__).resolve().parent / "corex_leads.db"
DEFAULT_OUT = Path(__file__).resolve().parent / "leads.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export qualified_leads to JSON")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output JSON path (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    conn = dbmod.get_connection(str(args.db))
    try:
        raw_leads = dbmod.get_all_leads(conn)
        qualified_leads = dbmod.get_all_qualified_leads(conn)
        tracker_rows = dbmod.get_all_tracker_rows(conn)
        export_log = dbmod.get_recent_exports(conn, 5)
    finally:
        conn.close()

    from datetime import datetime, timezone

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(args.db.resolve()),
        "count": len(qualified_leads),
        "leads": qualified_leads,
        "qualified_leads": qualified_leads,
        "raw_leads": raw_leads,
        "tracker_rows": tracker_rows,
        "exports": export_log,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(
        f"Wrote bundle to {args.output} "
        f"(raw={len(raw_leads)}, qualified={len(qualified_leads)}, "
        f"tracker={len(tracker_rows)})"
    )


if __name__ == "__main__":
    main()
