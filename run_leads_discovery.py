#!/usr/bin/env python3
"""
Run the full leads discovery pipeline (Finder → Qualifier) into corex_leads.db.

  python run_leads_discovery.py
  python run_leads_discovery.py --cities cities.csv --db /path/to/corex_leads.db
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "corex_leads.db"
DEFAULT_CITIES = ROOT / "cities.csv"
CACHE_DIR = ROOT / ".cache" / "discovery"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Finder + Qualifier into SQLite")
    parser.add_argument("--cities", type=Path, default=DEFAULT_CITIES)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    if not args.cities.exists():
        print(f"Cities file not found: {args.cities}", file=sys.stderr)
        return 1

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    raw_csv = CACHE_DIR / "leads_raw.csv"
    enriched_csv = CACHE_DIR / "leads_enriched.csv"
    db = str(args.db.resolve())

    py = sys.executable
    print("Stage 1: Finder (Google Places)…", flush=True)
    subprocess.check_call(
        [py, str(ROOT / "finder_places.py"), str(args.cities), str(raw_csv), "--db", db],
        cwd=str(ROOT),
    )
    print("Stage 2: Qualifier (website enrichment)…", flush=True)
    subprocess.check_call(
        [
            py,
            str(ROOT / "lead_qualifier.py"),
            str(raw_csv),
            str(enriched_csv),
            "--db",
            db,
        ],
        cwd=str(ROOT),
    )
    print(f"Discovery complete. Database: {db}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
