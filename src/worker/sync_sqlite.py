"""Copy pipeline SQLite results into Postgres with run/site scoping."""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db as dbmod  # noqa: E402
from psycopg import Connection

from src.db import repository as repo
from src.log import get_logger, log_action

logger = get_logger(__name__)


def _row_to_str_dict(row: sqlite3.Row) -> dict[str, str]:
    return {key: "" if row[key] is None else str(row[key]) for key in row.keys()}


def sync_sqlite_to_postgres(
    conn: Connection,
    sqlite_path: Path,
    *,
    run_id: UUID,
    site_id: str,
) -> dict[str, int]:
    """
    Import leads + qualified_leads from a pipeline SQLite file into Postgres.

    Returns counts synced per table.
    """
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite pipeline DB not found: {sqlite_path}")

    sqlite_conn = sqlite3.connect(str(sqlite_path))
    sqlite_conn.row_factory = sqlite3.Row
    try:
        lead_rows = sqlite_conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
        qualified_rows = sqlite_conn.execute(
            "SELECT * FROM qualified_leads ORDER BY id"
        ).fetchall()
    finally:
        sqlite_conn.close()

    lead_count = 0
    for row in lead_rows:
        data = _row_to_str_dict(row)
        repo.upsert_lead(conn, data, run_id=run_id, site_id=site_id)
        lead_count += 1

    qualified_count = 0
    for row in qualified_rows:
        data = _row_to_str_dict(row)
        for drop in ("id", "lead_id", "qualified_at"):
            data.pop(drop, None)
        repo.upsert_qualified_lead(conn, data, run_id=run_id, site_id=site_id)
        qualified_count += 1

    log_action(
        logger,
        logging.INFO,
        "SYNC",
        f"run/{run_id}",
        {"leads": lead_count, "qualified_leads": qualified_count},
        traces=[("ok", "synced SQLite pipeline to Postgres")],
    )
    return {"leads": lead_count, "qualified_leads": qualified_count}


def init_empty_sqlite(sqlite_path: Path) -> None:
    """Create an empty pipeline SQLite database."""
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    if sqlite_path.exists():
        sqlite_path.unlink()
    dbmod.init_db(str(sqlite_path))
