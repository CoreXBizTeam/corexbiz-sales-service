#!/usr/bin/env python3
"""
Source-agnostic lead list pipeline (dev).

  python run_lead_pipeline.py --config /path/to/run.json

Config JSON:
  run_id, list_name, source_type, criteria, output_stub (optional)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "corex_leads.db"
DEFAULT_CITIES = ROOT / "cities.csv"
RUNS_DIR = ROOT / "runs"

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT))
from src.log import configure_logging, get_logger, log_action

configure_logging()
logger = get_logger("pipeline")


def _filter_cities(cities_path: Path, provinces: List[str], out_path: Path) -> None:
    wanted = {p.strip().upper() for p in provinces if p.strip()}
    with cities_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if wanted:
        rows = [
            r
            for r in rows
            if str(r.get("province", "")).strip().upper() in wanted
        ]
    if not rows:
        raise RuntimeError("No cities left after province filter")
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["province", "city"])
        w.writeheader()
        w.writerows(rows)


def _run_google_maps(
    criteria: Dict[str, Any], raw_csv: Path, db: str, *, run_id: str = ""
) -> None:
    from src.config.env import google_maps_configured, google_maps_config_error

    if not google_maps_configured():
        err = google_maps_config_error()
        raise RuntimeError(err["message"])
    cities_file = Path(criteria.get("cities_file") or DEFAULT_CITIES)
    if not cities_file.is_absolute():
        cities_file = ROOT / cities_file
    provinces = criteria.get("provinces") or []
    seed = raw_csv.parent / "seed_cities.csv"
    if provinces:
        _filter_cities(cities_file, provinces, seed)
        cities_arg = str(seed)
    else:
        cities_arg = str(cities_file)
    enriched_csv = raw_csv.parent / "leads_enriched.csv"
    py = sys.executable
    log_action(
        logger,
        logging.INFO,
        "RUN",
        f"run/{run_id}" if run_id else "stage/finder",
        {"stage": "finder", "cities": cities_arg, "output": str(raw_csv)},
        traces=[("start", "finder_places.py")],
    )
    subprocess.check_call(
        [py, str(ROOT / "finder_places.py"), cities_arg, str(raw_csv), "--db", db],
        cwd=str(ROOT),
    )
    log_action(
        logger,
        logging.INFO,
        "RUN",
        f"run/{run_id}" if run_id else "stage/finder",
        {"stage": "finder", "output": str(raw_csv)},
        traces=[("done", "finder_places.py")],
    )
    log_action(
        logger,
        logging.INFO,
        "RUN",
        f"run/{run_id}" if run_id else "stage/qualifier",
        {"stage": "qualifier", "input": str(raw_csv), "output": str(enriched_csv)},
        traces=[("start", "lead_qualifier.py")],
    )
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
    log_action(
        logger,
        logging.INFO,
        "RUN",
        f"run/{run_id}" if run_id else "stage/qualifier",
        {"stage": "qualifier", "output": str(enriched_csv)},
        traces=[("done", "lead_qualifier.py")],
    )


def _run_manual_csv(criteria: Dict[str, Any], raw_csv: Path, db: str) -> None:
    src = Path(str(criteria.get("csv_path", "")).strip())
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        raise RuntimeError(f"CSV not found: {src}")
    shutil.copy2(src, raw_csv)
    enriched_csv = raw_csv.parent / "leads_enriched.csv"
    py = sys.executable
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


def _run_custom_script(criteria: Dict[str, Any], raw_csv: Path, db: str) -> None:
    script = Path(str(criteria.get("script_path", "")).strip())
    if not script.is_absolute():
        script = ROOT / script
    if not script.exists():
        raise RuntimeError(f"Script not found: {script}")
    extra = str(criteria.get("extra_args", "")).strip()
    py = sys.executable
    cmd = [py, str(script), "--out", str(raw_csv)]
    if extra:
        cmd.extend(extra.split())
    subprocess.check_call(cmd, cwd=str(ROOT))
    enriched_csv = raw_csv.parent / "leads_enriched.csv"
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a source-agnostic lead list job")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_id = str(config.get("run_id") or uuid.uuid4())
    source_type = str(config.get("source_type", "")).strip()
    criteria = dict(config.get("criteria") or {})
    db = str(args.db.resolve())

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stub = config.get("output_stub")
    run_dir = Path(stub) if stub else RUNS_DIR / f"{run_id}_{source_type}"
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_csv = run_dir / f"{run_id}_raw.csv"

    log_action(
        logger,
        logging.INFO,
        "RUN",
        f"run/{run_id}",
        {"source_type": source_type, "list_name": config.get("list_name"), "stage": "pipeline"},
        traces=[("start", "pipeline job accepted")],
    )

    if source_type == "google_maps":
        _run_google_maps(criteria, raw_csv, db, run_id=run_id)
    elif source_type == "manual_csv":
        _run_manual_csv(criteria, raw_csv, db)
    elif source_type == "custom_script":
        _run_custom_script(criteria, raw_csv, db)
    elif source_type == "google_web":
        raise RuntimeError(
            "google_web adapter not implemented. Add sources/google_web_search.py."
        )
    else:
        raise RuntimeError(f"Unknown source_type: {source_type}")

    log_action(
        logger,
        logging.INFO,
        "RUN",
        f"run/{run_id}",
        {"output_dir": str(run_dir), "stage": "pipeline"},
        traces=[("done", "pipeline finished")],
    )
    print(json.dumps({"run_id": run_id, "output_dir": str(run_dir), "status": "completed"}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
