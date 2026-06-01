#!/usr/bin/env python3
"""
db.py — SQLite persistence for the CoreX sales pipeline (finder → qualifier → export).

All raw SQL lives here. Other scripts call these functions only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Finder CSV columns (must match finder_places.OUTPUT_FIELDNAMES).
LEADS_COLUMN_NAMES: Tuple[str, ...] = (
    "business_name",
    "website",
    "address",
    "city",
    "province",
    "zip",
    "country",
    "source",
    "search_query",
    "place_id",
    "vicinity",
    "latitude",
    "longitude",
    "formatted_phone_number",
    "international_phone_number",
    "rating",
    "user_ratings_total",
    "price_level",
    "google_maps_url",
    "business_status",
    "opening_hours_available",
    "photos_count",
    "primary_type",
    "editorial_summary",
    "types",
    "serves_beer",
    "serves_breakfast",
    "serves_brunch",
    "serves_dinner",
    "serves_lunch",
    "serves_vegetarian_food",
    "serves_wine",
)

# Qualifier-prefixed columns (must match lead_qualifier.OUTPUT_COLUMN_PREFIX).
QUALIFIER_PREFIX_COLUMNS: Tuple[str, ...] = (
    "province_normalized",
    "city",
    "province",
    "prov",
    "business_name",
    "website",
    "normalized_url",
    "wordpress_detected",
    "woocommerce_detected",
    "wordpress_sort",
    "woocommerce_sort",
    "priority_score_sort",
    "priority_score",
    "priority_segment",
    "final_url",
    "reachable",
    "http_status",
    "platform",
    "ecommerce",
    "upload_present",
    "upload_signals",
    "email_found",
    "fit_segment",
    "fit_score",
    "notes",
)

QUALIFIED_FINDER_TAIL: Tuple[str, ...] = tuple(
    c for c in LEADS_COLUMN_NAMES if c not in QUALIFIER_PREFIX_COLUMNS
)

QUALIFIED_DATA_COLUMNS: Tuple[str, ...] = tuple(
    dict.fromkeys(list(QUALIFIER_PREFIX_COLUMNS) + list(QUALIFIED_FINDER_TAIL))
)

# `website` is its own column on qualified_leads (before data payload); omit duplicate.
QUALIFIED_ROW_COLUMNS: Tuple[str, ...] = tuple(c for c in QUALIFIED_DATA_COLUMNS if c != "website")

# Human curation on qualified_leads (Streamlit Review tab).
REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_REJECTED = "rejected"
REVIEW_STATUS_VALUES: Tuple[str, ...] = (
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_REJECTED,
)

# Lead tracker CSV column order (must match export_lead_tracker / headerless tracker exports).
# Empty string = leading blank column in Sheets; stored as sheet_blank in SQLite.
TRACKER_CSV_HEADERS: Tuple[str, ...] = (
    "",
    "Company Name",
    "Website",
    "Contact Name",
    "Contact Email",
    "Contact Phone",
    "Business Type",
    "Has E-commerce?",
    "Has Physical Store?",
    "E-Commerce Platform",
    "Offers Framing?",
    "Offers Canvas?",
    "Offers Acrylic?",
    "Fit Tier",
    "Notes",
    "Address",
    "City",
    "State",
    "Zip",
    "Country",
    "LinkedIn",
    "Lead Source",
    "Priority Score",
    "Segment",
    "Outreach Status",
    "Last Contact Date",
    "Next Step",
    "Next Contact Date",
    "Response Status",
    "Demo Booked",
    "Demo Date",
    "Qualified",
    "Proposal Amount",
    "Interest Level",
    "Close Date",
    "Final Outcome",
    "Website Platform",
    "Assigned To",
    "Assignment Date",
    "Lead Owner Lock",
    "Target Segment",
    "Source",
)


def _tracker_sql_column_name(header: str) -> str:
    if header == "":
        return "sheet_blank"
    return header


def _tracker_dedupe_key(row: Dict[str, str]) -> str:
    biz = _row_get(row, "Company Name").strip().lower()
    web = _row_get(row, "Website").strip().lower()
    return f"{biz}|{web}"


# Columns that must exist for upserts / uniqueness; if an old DB is missing these, recreate the table.
_QUALIFIED_LEADS_REQUIRED: Tuple[str, ...] = (
    "lead_id",
    "website",
    "qualified_at",
    "dedupe_key",
    "fit_tier",
)


def _qualified_leads_expected_columns() -> Tuple[str, ...]:
    """All non-id columns defined on qualified_leads (matches CREATE body after id)."""
    return _QUALIFIED_LEADS_REQUIRED + ("review_status",) + QUALIFIED_ROW_COLUMNS


def default_db_path() -> str:
    """Project root `corex_leads.db` (directory containing this file)."""
    return str(Path(__file__).resolve().parent / "corex_leads.db")


def strip_db_arg(argv: List[str]) -> Tuple[List[str], Optional[str]]:
    """Remove ``--db`` / ``--db path`` from CLI args; return (new_argv, db_path or None).

    If ``--db`` has no following path, uses :func:`default_db_path`.
    """
    if "--db" not in argv:
        return list(argv), None
    out: List[str] = []
    db_path: Optional[str] = None
    i = 0
    while i < len(argv):
        if argv[i] == "--db":
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                db_path = argv[i + 1]
                i += 2
            else:
                db_path = default_db_path()
                i += 1
        else:
            out.append(argv[i])
            i += 1
    return out, db_path


def _sql_quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row_get(d: Dict[str, str], key: str) -> str:
    v = d.get(key)
    return "" if v is None else str(v)


def _fit_tier_from_segment(fit_segment: str) -> str:
    t = (fit_segment or "").lower()
    if "strong fit" in t:
        return "Tier 1 (Ideal)"
    if "secondary fit" in t or "possible fit" in t:
        return "Tier 2 (Good)"
    if "review manually" in t or "unreachable" in t:
        return "Tier 3 (Low)"
    if (fit_segment or "").strip():
        return "Tier 2 (Good)"
    return ""


def _qualified_dedupe_key(row: Dict[str, str]) -> str:
    """Stable unique key for INSERT OR REPLACE (avoids collapsing many empty websites)."""
    web = (_row_get(row, "normalized_url") or _row_get(row, "website") or "").strip().lower()
    if web:
        return web
    pid = _row_get(row, "place_id").strip()
    if pid:
        return f"place:{pid}"
    name = _row_get(row, "business_name").strip().lower()
    city = _row_get(row, "city").strip().lower()
    return f"anon:{name}|{city}"


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.execute(f"PRAGMA table_info({_sql_quote_ident(table)})")
    return [str(r[1]) for r in cur.fetchall()]


def _migrate_tracker_rows_columns(conn: sqlite3.Connection) -> None:
    """Add missing tracker_rows columns when TRACKER_CSV_HEADERS grows."""
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracker_rows' LIMIT 1"
        )
        if cur.fetchone() is None:
            return
    except sqlite3.Error:
        return

    have = set(_table_columns(conn, "tracker_rows"))
    for h in TRACKER_CSV_HEADERS:
        sqlc = _tracker_sql_column_name(h)
        if sqlc in have:
            continue
        conn.execute(
            f"ALTER TABLE tracker_rows ADD COLUMN {_sql_quote_ident(sqlc)} TEXT"
        )


def _migrate_qualified_leads_columns(conn: sqlite3.Connection) -> None:
    """Add any missing qualified_leads columns (TEXT) expected by the current code."""
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='qualified_leads' LIMIT 1"
        )
        if cur.fetchone() is None:
            return
    except sqlite3.Error:
        return

    have = set(_table_columns(conn, "qualified_leads"))
    expected = _qualified_leads_expected_columns()
    for col in expected:
        if col in have:
            continue
        if col == "review_status":
            conn.execute(
                "ALTER TABLE qualified_leads ADD COLUMN "
                f"{_sql_quote_ident(col)} TEXT DEFAULT '{REVIEW_STATUS_PENDING}'"
            )
        else:
            conn.execute(
                f"ALTER TABLE qualified_leads ADD COLUMN {_sql_quote_ident(col)} TEXT"
            )
    conn.execute(
        """
        UPDATE qualified_leads
        SET review_status = ?
        WHERE review_status IS NULL OR trim(review_status) = ''
        """,
        (REVIEW_STATUS_PENDING,),
    )


def migrate_db(db_path: str) -> None:
    """Apply schema updates to an existing database (safe: ADD COLUMN only)."""
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        with conn:
            _migrate_qualified_leads_columns(conn)
            _migrate_tracker_rows_columns(conn)
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.migrate_db failed for {db_path!r}: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def get_connection(db_path: str) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.get_connection failed for {db_path!r}: {exc}") from exc


def init_db(db_path: str) -> None:
    leads_body = ", ".join(f"{_sql_quote_ident(c)} TEXT" for c in LEADS_COLUMN_NAMES)
    qual_body = ", ".join(f"{_sql_quote_ident(c)} TEXT" for c in QUALIFIED_ROW_COLUMNS)
    tracker_body = ", ".join(
        f"{_sql_quote_ident(_tracker_sql_column_name(h))} TEXT"
        for h in TRACKER_CSV_HEADERS
    )
    qualified_ddl = f"""
                CREATE TABLE qualified_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER REFERENCES leads(id),
                    website TEXT,
                    qualified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    fit_tier TEXT,
                    review_status TEXT DEFAULT '{REVIEW_STATUS_PENDING}',
                    {qual_body}
                );
                """
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        with conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    {leads_body},
                    UNIQUE(place_id)
                );
                """
            )
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='qualified_leads' LIMIT 1"
            )
            if cur.fetchone() is not None:
                cols = set(_table_columns(conn, "qualified_leads"))
                required = set(_QUALIFIED_LEADS_REQUIRED)
                expected = set(_qualified_leads_expected_columns())
                # Incompatible legacy table (cannot upsert): recreate empty table.
                if not required.issubset(cols):
                    conn.execute("DROP TABLE qualified_leads")
                    conn.execute(qualified_ddl)
                elif not expected.issubset(cols):
                    _migrate_qualified_leads_columns(conn)
            else:
                conn.execute(qualified_ddl)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    row_count INTEGER,
                    output_path TEXT,
                    notes TEXT
                );
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS tracker_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    {tracker_body}
                );
                """
            )
            _migrate_qualified_leads_columns(conn)
            _migrate_tracker_rows_columns(conn)
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.init_db failed for {db_path!r}: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def upsert_lead(conn: sqlite3.Connection, row_dict: Dict[str, str]) -> None:
    cols: List[str] = ["place_id"] + [c for c in LEADS_COLUMN_NAMES if c != "place_id"]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(_sql_quote_ident(c) for c in cols)
    sql = f"INSERT OR IGNORE INTO leads ({col_names}) VALUES ({placeholders})"
    vals = [_row_get(row_dict, c) for c in cols]
    try:
        conn.execute(sql, vals)
        conn.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.upsert_lead failed: {exc}") from exc


def upsert_tracker_row(conn: sqlite3.Connection, row_dict: Dict[str, str]) -> None:
    """Insert or replace one tracker CSV row; dedupe on Company Name + Website."""
    dedupe = _tracker_dedupe_key(row_dict)
    storage_cols = [_tracker_sql_column_name(h) for h in TRACKER_CSV_HEADERS]
    cols_q: List[str] = ["dedupe_key"] + storage_cols
    placeholders = ", ".join(["?"] * len(cols_q))
    col_names = ", ".join(_sql_quote_ident(c) for c in cols_q)
    assignments = ", ".join(
        f"{_sql_quote_ident(c)} = excluded.{_sql_quote_ident(c)}"
        for c in cols_q
        if c != "dedupe_key"
    )
    sql = (
        f"INSERT INTO tracker_rows ({col_names}) VALUES ({placeholders}) "
        f"ON CONFLICT(dedupe_key) DO UPDATE SET {assignments}, "
        f"imported_at = CURRENT_TIMESTAMP"
    )
    vals: List[object] = [dedupe]
    vals.extend(_row_get(row_dict, h) for h in TRACKER_CSV_HEADERS)
    try:
        conn.execute(sql, vals)
        conn.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.upsert_tracker_row failed: {exc}") from exc


def _resolve_lead_id(conn: sqlite3.Connection, place_id: str) -> Optional[int]:
    if not (place_id or "").strip():
        return None
    try:
        cur = conn.execute(
            "SELECT id FROM leads WHERE place_id = ? LIMIT 1",
            (place_id.strip(),),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error as exc:
        raise RuntimeError(f"db._resolve_lead_id failed: {exc}") from exc


def upsert_qualified_lead(conn: sqlite3.Connection, row_dict: Dict[str, str]) -> None:
    dedupe = _qualified_dedupe_key(row_dict)
    web_display = _row_get(row_dict, "website") or _row_get(row_dict, "normalized_url")
    fit_tier = _row_get(row_dict, "fit_tier") or _fit_tier_from_segment(_row_get(row_dict, "fit_segment"))
    lead_id = _resolve_lead_id(conn, _row_get(row_dict, "place_id"))

    cols_q: List[str] = ["lead_id", "website", "dedupe_key", "fit_tier"] + list(QUALIFIED_ROW_COLUMNS)
    placeholders = ", ".join(["?"] * len(cols_q))
    col_names = ", ".join(_sql_quote_ident(c) for c in cols_q)
    assignments = ", ".join(
        f"{_sql_quote_ident(c)} = excluded.{_sql_quote_ident(c)}"
        for c in cols_q
        if c not in ("id",)
    )
    sql = (
        f"INSERT INTO qualified_leads ({col_names}) VALUES ({placeholders}) "
        f"ON CONFLICT(dedupe_key) DO UPDATE SET {assignments}, "
        f"qualified_at = CURRENT_TIMESTAMP"
    )
    vals: List[object] = [lead_id, web_display, dedupe, fit_tier]
    vals.extend(_row_get(row_dict, c) for c in QUALIFIED_ROW_COLUMNS)
    try:
        conn.execute(sql, vals)
        # Default isolation_level ("") uses implicit transactions; close() rolls back without commit.
        conn.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.upsert_qualified_lead failed: {exc}") from exc


def log_export(conn: sqlite3.Connection, row_count: int, output_path: str, notes: str) -> None:
    try:
        conn.execute(
            "INSERT INTO exports (row_count, output_path, notes) VALUES (?, ?, ?)",
            (row_count, output_path, notes or ""),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.log_export failed: {exc}") from exc


def _rows_to_dicts(rows: Sequence[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def get_all_leads(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    try:
        cur = conn.execute("SELECT * FROM leads ORDER BY id")
        return _rows_to_dicts(cur.fetchall())
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.get_all_leads failed: {exc}") from exc


def get_all_qualified_leads(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    try:
        cur = conn.execute("SELECT * FROM qualified_leads ORDER BY id")
        return _rows_to_dicts(cur.fetchall())
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.get_all_qualified_leads failed: {exc}") from exc


def get_qualified_by_review_status(
    conn: sqlite3.Connection,
    status: str,
) -> List[Dict[str, Any]]:
    """Return qualified rows where ``review_status`` matches (or all if ``status`` is ``All``)."""
    s = (status or "").strip()
    if not s or s.lower() == "all":
        return get_all_qualified_leads(conn)
    key = s.lower()
    if key not in REVIEW_STATUS_VALUES:
        raise ValueError(
            f"status must be one of {REVIEW_STATUS_VALUES!r} or 'All', got {status!r}"
        )
    try:
        cur = conn.execute(
            """
            SELECT * FROM qualified_leads
            WHERE lower(coalesce(review_status, ?)) = ?
            ORDER BY id
            """,
            (REVIEW_STATUS_PENDING, key),
        )
        return _rows_to_dicts(cur.fetchall())
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.get_qualified_by_review_status failed: {exc}") from exc


def set_review_status(
    conn: sqlite3.Connection,
    lead_id: int,
    status: str,
) -> None:
    """Set ``review_status`` for one ``qualified_leads`` row by primary key ``id``."""
    key = (status or "").strip().lower()
    if key not in REVIEW_STATUS_VALUES:
        raise ValueError(f"status must be one of {REVIEW_STATUS_VALUES!r}, got {status!r}")
    try:
        cur = conn.execute(
            "UPDATE qualified_leads SET review_status = ? WHERE id = ?",
            (key, int(lead_id)),
        )
        if cur.rowcount == 0:
            raise RuntimeError(f"no qualified_leads row with id={lead_id}")
        conn.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.set_review_status failed: {exc}") from exc


def bulk_set_review_status(
    conn: sqlite3.Connection,
    lead_ids: Sequence[int],
    status: str,
) -> None:
    """Set ``review_status`` for many ``qualified_leads`` rows by primary key ``id``."""
    key = (status or "").strip().lower()
    if key not in REVIEW_STATUS_VALUES:
        raise ValueError(f"status must be one of {REVIEW_STATUS_VALUES!r}, got {status!r}")
    ids = [int(i) for i in lead_ids]
    if not ids:
        return
    try:
        conn.executemany(
            "UPDATE qualified_leads SET review_status = ? WHERE id = ?",
            [(key, i) for i in ids],
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.bulk_set_review_status failed: {exc}") from exc


def get_tracker_rows_approved_qualified(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Tracker rows whose ``dedupe_key`` matches a qualified lead with ``review_status = approved``.

    Join is on ``dedupe_key`` (same as import pipeline). Rows without a matching approved
    qualified lead are omitted.
    """
    try:
        cur = conn.execute(
            """
            SELECT t.*
            FROM tracker_rows t
            INNER JOIN qualified_leads q ON q.dedupe_key = t.dedupe_key
            WHERE lower(coalesce(q.review_status, ?)) = ?
            ORDER BY t.id
            """,
            (REVIEW_STATUS_PENDING, REVIEW_STATUS_APPROVED),
        )
        return _rows_to_dicts(cur.fetchall())
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.get_tracker_rows_approved_qualified failed: {exc}") from exc


def get_qualified_by_tier(conn: sqlite3.Connection, tier: str) -> List[Dict[str, Any]]:
    t = (tier or "").strip()
    if not t:
        return []
    try:
        cur = conn.execute(
            """
            SELECT * FROM qualified_leads
            WHERE fit_tier = ? OR fit_segment LIKE '%' || ? || '%'
            ORDER BY id
            """,
            (t, t),
        )
        return _rows_to_dicts(cur.fetchall())
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.get_qualified_by_tier failed: {exc}") from exc


def get_all_tracker_rows(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    try:
        cur = conn.execute("SELECT * FROM tracker_rows ORDER BY id")
        return _rows_to_dicts(cur.fetchall())
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.get_all_tracker_rows failed: {exc}") from exc


def get_recent_exports(conn: sqlite3.Connection, limit: int = 5) -> List[Dict[str, Any]]:
    lim = max(1, min(int(limit), 500))
    try:
        cur = conn.execute(
            """
            SELECT id, exported_at, row_count, output_path, notes
            FROM exports
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim,),
        )
        return _rows_to_dicts(cur.fetchall())
    except sqlite3.Error as exc:
        raise RuntimeError(f"db.get_recent_exports failed: {exc}") from exc


def tracker_row_csv_values(row: Dict[str, Any]) -> List[str]:
    """One tracker_rows DB row → cell values in TRACKER_CSV_HEADERS order (for headerless CSV)."""
    return [
        str(row.get(_tracker_sql_column_name(h)) or "")
        for h in TRACKER_CSV_HEADERS
    ]


if __name__ == "__main__":
    import os
    import tempfile

    _db_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        init_db(_db_path)
        migrate_db(_db_path)
        _conn = get_connection(_db_path)
        try:
            _test_row: Dict[str, str] = {
                "business_name": "Test Shop",
                "website": "http://testshop.ca",
                "normalized_url": "http://testshop.ca/",
                "place_id": "test123",
                "fit_segment": "Strong Fit: WP without visible upload",
                "fit_score": "80",
            }
            upsert_qualified_lead(_conn, _test_row)
            _cur = _conn.execute("SELECT COUNT(*) FROM qualified_leads")
            _count = int(_cur.fetchone()[0])
            print(f"Test result: {_count} row(s) in qualified_leads")
            assert _count == 1, "FAILED — upsert did not persist after commit"
            print("PASSED")
        finally:
            _conn.close()
    finally:
        os.unlink(_db_path)
