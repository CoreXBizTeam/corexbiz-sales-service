#!/usr/bin/env python3
"""
import_to_db.py — Load existing CSVs into corex_leads.db without running the pipeline.

Usage:
  python import_to_db.py --leads output_bc/leads_bc.csv \\
    --qualified output_bc/leads_bc_enriched.csv \\
    --tracker output_bc/leads_bc_tracker.csv --db
"""

from __future__ import annotations

import argparse
import csv
import sys
from typing import Dict, List, Optional

import db as dbmod


def _read_csv_with_header(path: str) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_tracker_csv(path: str) -> List[Dict[str, str]]:
    """Tracker exports are headerless; bind columns to db.TRACKER_CSV_HEADERS."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(
            csv.DictReader(f, fieldnames=list(dbmod.TRACKER_CSV_HEADERS))
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import CSVs into the CoreX SQLite database.",
    )
    parser.add_argument("--leads", metavar="PATH", help="Finder-style leads CSV")
    parser.add_argument("--qualified", metavar="PATH", help="Qualifier enriched CSV")
    parser.add_argument("--tracker", metavar="PATH", help="Lead tracker CSV (headerless)")
    parser.add_argument(
        "--db",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="SQLite path (default: project corex_leads.db). Use --db alone for default.",
    )
    args = parser.parse_args()

    if not any((args.leads, args.qualified, args.tracker)):
        parser.print_help()
        print(
            "\nAt least one of --leads, --qualified, or --tracker is required.",
            file=sys.stderr,
        )
        return 1

    db_path = dbmod.default_db_path() if args.db in (None, "") else args.db

    dbmod.init_db(db_path)
    dbmod.migrate_db(db_path)
    conn = dbmod.get_connection(db_path)

    n_leads = n_qualified = n_tracker = 0

    try:
        if args.leads:
            rows = _read_csv_with_header(args.leads)
            for i, row in enumerate(rows, start=1):
                dbmod.upsert_lead(conn, row)
                if i % 100 == 0:
                    print(f"  leads: processed {i} rows...", flush=True)
            n_leads = len(rows)

        if args.qualified:
            rows = _read_csv_with_header(args.qualified)
            for i, row in enumerate(rows, start=1):
                dbmod.upsert_qualified_lead(conn, row)
                if i % 100 == 0:
                    print(f"  qualified: processed {i} rows...", flush=True)
            n_qualified = len(rows)

        if args.tracker:
            rows = _read_tracker_csv(args.tracker)
            for i, row in enumerate(rows, start=1):
                dbmod.upsert_tracker_row(conn, row)
                if i % 100 == 0:
                    print(f"  tracker: processed {i} rows...", flush=True)
            n_tracker = len(rows)

    finally:
        conn.close()

    print(
        f"Imported {n_leads} leads, {n_qualified} qualified leads, "
        f"{n_tracker} tracker rows into {db_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
