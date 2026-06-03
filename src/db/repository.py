"""
Postgres repository for CoreX Sales Service.

Mirrors db.py operations against Cloud SQL (search_path = POSTGRES_SCHEMA).
SQLite pipeline scripts continue to use db.py until Phase 3 workers write here directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg import Connection

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db as dbmod  # noqa: E402


def _quote_ident(name: str) -> str:
    return dbmod._sql_quote_ident(name)


def _rows_to_dicts(cursor) -> list[dict[str, Any]]:
    if cursor.description is None:
        return []
    cols = [d.name for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _resolve_lead_id(conn: Connection, place_id: str) -> int | None:
    pid = (place_id or "").strip()
    if not pid:
        return None
    row = conn.execute(
        "SELECT id FROM leads WHERE place_id = %s LIMIT 1",
        (pid,),
    ).fetchone()
    return int(row[0]) if row else None


def upsert_lead(
    conn: Connection,
    row_dict: dict[str, str],
    *,
    run_id: UUID | None = None,
    site_id: str | None = None,
) -> None:
    cols = ["place_id"] + [c for c in dbmod.LEADS_COLUMN_NAMES if c != "place_id"]
    extra_cols: list[str] = []
    if run_id is not None:
        extra_cols = ["run_id", "site_id"]
    all_cols = extra_cols + cols
    col_names = ", ".join(_quote_ident(c) for c in all_cols)
    placeholders = ", ".join(["%s"] * len(all_cols))
    vals: list[Any] = []
    if run_id is not None:
        vals.extend([run_id, site_id])
    vals.extend(dbmod._row_get(row_dict, c) for c in cols)
    sql = f"INSERT INTO leads ({col_names}) VALUES ({placeholders}) ON CONFLICT (place_id) DO NOTHING"
    conn.execute(sql, vals)


def upsert_qualified_lead(
    conn: Connection,
    row_dict: dict[str, str],
    *,
    run_id: UUID | None = None,
    site_id: str | None = None,
) -> None:
    dedupe = dbmod._qualified_dedupe_key(row_dict)
    web_display = dbmod._row_get(row_dict, "website") or dbmod._row_get(row_dict, "normalized_url")
    fit_tier = dbmod._row_get(row_dict, "fit_tier") or dbmod._fit_tier_from_segment(
        dbmod._row_get(row_dict, "fit_segment")
    )
    review_status = dbmod._row_get(row_dict, "review_status") or dbmod.REVIEW_STATUS_PENDING
    review_status = review_status.strip().lower()
    if review_status not in dbmod.REVIEW_STATUS_VALUES:
        review_status = dbmod.REVIEW_STATUS_PENDING
    lead_id = _resolve_lead_id(conn, dbmod._row_get(row_dict, "place_id"))

    cols = ["lead_id", "website", "dedupe_key", "fit_tier", "review_status"] + list(
        dbmod.QUALIFIED_ROW_COLUMNS
    )
    if run_id is not None:
        cols = ["run_id", "site_id"] + cols
    col_names = ", ".join(_quote_ident(c) for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    update_cols = [c for c in cols if c not in ("dedupe_key",)]
    assignments = ", ".join(
        f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}" for c in update_cols
    )
    vals: list[Any] = []
    if run_id is not None:
        vals.extend([run_id, site_id])
    vals.extend([lead_id, web_display, dedupe, fit_tier, review_status])
    vals.extend(dbmod._row_get(row_dict, c) for c in dbmod.QUALIFIED_ROW_COLUMNS)

    sql = (
        f"INSERT INTO qualified_leads ({col_names}) VALUES ({placeholders}) "
        f"ON CONFLICT (dedupe_key) DO UPDATE SET {assignments}, "
        f"qualified_at = NOW()"
    )
    conn.execute(sql, vals)


def log_export(
    conn: Connection,
    row_count: int,
    output_path: str,
    notes: str,
    *,
    run_id: UUID | None = None,
    site_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO exports (row_count, output_path, notes, run_id, site_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (row_count, output_path, notes or "", run_id, site_id),
    )


def get_all_leads(conn: Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM leads ORDER BY id")
    return _rows_to_dicts(cur)


def get_all_qualified_leads(conn: Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM qualified_leads ORDER BY id")
    return _rows_to_dicts(cur)


def get_recent_exports(conn: Connection, limit: int = 5) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 500))
    cur = conn.execute(
        """
        SELECT id, exported_at, row_count, output_path, notes
        FROM exports
        ORDER BY id DESC
        LIMIT %s
        """,
        (lim,),
    )
    return _rows_to_dicts(cur)


def persist_run_result(
    conn: Connection,
    *,
    run_id: UUID,
    site_id: str,
    site_url: str | None,
    list_name: str | None,
    source_type: str,
    criteria: dict[str, Any],
    notes: str = "",
    webhook_url: str | None = None,
    status: str,
    error: str | None = None,
    message: str | None = None,
    started_at: Any | None = None,
) -> None:
    """Write a finished run record once (completed or failed)."""
    conn.execute(
        """
        INSERT INTO runs (
          id, site_id, site_url, list_name, source_type, criteria, notes, webhook_url,
          status, error, message, started_at, finished_at
        ) VALUES (
          %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
          %s, %s, %s, COALESCE(%s, NOW()), NOW()
        )
        """,
        (
            run_id,
            site_id,
            site_url,
            list_name,
            source_type,
            json.dumps(criteria),
            notes,
            webhook_url,
            status,
            error,
            message,
            started_at,
        ),
    )
    from src.log.run_trace import log_run_progress

    log_run_progress(
        run_id,
        status,
        source_type=source_type,
        site_id=site_id,
        message=message,
        error=error,
        stage="persist",
        traces=[("saved", f"run result persisted ({status})")],
    )


def mark_webhook_sent(conn: Connection, run_id: UUID) -> None:
    conn.execute(
        "UPDATE runs SET webhook_sent_at = NOW() WHERE id = %s",
        (run_id,),
    )


def count_qualified_for_run(conn: Connection, run_id: UUID) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM qualified_leads WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def default_webhook_url(site_url: str | None) -> str | None:
    base = (site_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/wp-json/corexbiz/v1/sales/run-webhook"


def get_run(conn: Connection, run_id: UUID) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
    rows = _rows_to_dicts(cur)
    return rows[0] if rows else None


def get_run_for_site(conn: Connection, run_id: UUID, site_id: str) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM runs WHERE id = %s AND site_id = %s",
        (run_id, site_id),
    )
    rows = _rows_to_dicts(cur)
    return rows[0] if rows else None


def _paginate(
    conn: Connection,
    table: str,
    *,
    site_id: str | None = None,
    run_id: UUID | None = None,
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    page = max(1, page)
    per_page = max(1, min(per_page, 500))
    offset = (page - 1) * per_page

    clauses: list[str] = []
    vals: list[Any] = []
    if site_id:
        clauses.append("site_id = %s")
        vals.append(site_id)
    if run_id is not None:
        clauses.append("run_id = %s")
        vals.append(run_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    count_cur = conn.execute(f"SELECT COUNT(*) FROM {table} {where}", vals)
    total = int(count_cur.fetchone()[0])

    cur = conn.execute(
        f"SELECT * FROM {table} {where} ORDER BY id LIMIT %s OFFSET %s",
        [*vals, per_page, offset],
    )
    return _rows_to_dicts(cur), total


def list_leads_for_run(
    conn: Connection,
    run_id: UUID,
    site_id: str,
    *,
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    return _paginate(
        conn, "leads", site_id=site_id, run_id=run_id, page=page, per_page=per_page
    )


def list_qualified_for_run(
    conn: Connection,
    run_id: UUID,
    site_id: str,
    *,
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    return _paginate(
        conn,
        "qualified_leads",
        site_id=site_id,
        run_id=run_id,
        page=page,
        per_page=per_page,
    )


def list_qualified_for_site(
    conn: Connection,
    site_id: str,
    *,
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    return _paginate(conn, "qualified_leads", site_id=site_id, page=page, per_page=per_page)


def list_raw_leads_for_site(
    conn: Connection,
    site_id: str,
    *,
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    return _paginate(conn, "leads", site_id=site_id, page=page, per_page=per_page)


def get_all_tracker_rows(conn: Connection, site_id: str | None = None) -> list[dict[str, Any]]:
    if site_id:
        cur = conn.execute(
            "SELECT * FROM tracker_rows WHERE site_id = %s ORDER BY id",
            (site_id,),
        )
    else:
        cur = conn.execute("SELECT * FROM tracker_rows ORDER BY id")
    return _rows_to_dicts(cur)


def get_qualified_lead(conn: Connection, lead_id: int, site_id: str) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM qualified_leads WHERE id = %s AND site_id = %s",
        (lead_id, site_id),
    )
    rows = _rows_to_dicts(cur)
    return rows[0] if rows else None


def update_qualified_review(
    conn: Connection,
    lead_id: int,
    site_id: str,
    review_status: str,
    notes: str,
) -> dict[str, Any] | None:
    key = (review_status or "").strip().lower()
    if key not in dbmod.REVIEW_STATUS_VALUES:
        raise ValueError(f"review_status must be one of {dbmod.REVIEW_STATUS_VALUES!r}")

    cur = conn.execute(
        """
        UPDATE qualified_leads
        SET review_status = %s, notes = %s
        WHERE id = %s AND site_id = %s
        RETURNING id, review_status, notes
        """,
        (key, notes or "", lead_id, site_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"id": int(row[0]), "review_status": row[1], "notes": row[2] or ""}


def run_to_status_payload(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "queued")
    return {
        **row,
        "run_id": row.get("id"),
        "running": status == "running",
        "status": status,
    }


def run_spec_from_request(
    *,
    run_id: UUID,
    site_id: str,
    site_url: str | None,
    list_name: str | None,
    source_type: str,
    criteria: dict[str, Any],
    notes: str = "",
    webhook_url: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(run_id),
        "site_id": site_id,
        "site_url": site_url,
        "list_name": list_name,
        "source_type": source_type,
        "criteria": criteria,
        "notes": notes,
        "webhook_url": webhook_url,
    }
