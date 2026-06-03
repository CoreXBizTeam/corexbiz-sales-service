#!/usr/bin/env python3
"""Generate Postgres migration SQL from db.py column contracts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbmod  # noqa: E402

MIGRATIONS = ROOT / "db" / "migrations"
SCHEMA = "sales-service"


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def schema_prefix() -> str:
    return f"{qident(SCHEMA)}."


def text_columns(names: tuple[str, ...]) -> str:
    return ",\n  ".join(f"{qident(n)} TEXT" for n in names)


def write(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    MIGRATIONS.mkdir(parents=True, exist_ok=True)
    sp = schema_prefix()

    write(
        MIGRATIONS / "003_leads.sql",
        f"""
CREATE TABLE IF NOT EXISTS {sp}leads (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  run_id UUID REFERENCES {sp}runs(id) ON DELETE SET NULL,
  site_id TEXT,
  {text_columns(dbmod.LEADS_COLUMN_NAMES)},
  UNIQUE (place_id)
);
CREATE INDEX IF NOT EXISTS leads_run_id_idx ON {sp}leads (run_id);
CREATE INDEX IF NOT EXISTS leads_site_id_idx ON {sp}leads (site_id);
""",
    )

    qual_cols = text_columns(dbmod.QUALIFIED_ROW_COLUMNS)
    write(
        MIGRATIONS / "004_qualified_leads.sql",
        f"""
CREATE TABLE IF NOT EXISTS {sp}qualified_leads (
  id BIGSERIAL PRIMARY KEY,
  lead_id BIGINT REFERENCES {sp}leads(id) ON DELETE SET NULL,
  website TEXT,
  qualified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dedupe_key TEXT NOT NULL,
  fit_tier TEXT,
  review_status TEXT NOT NULL DEFAULT '{dbmod.REVIEW_STATUS_PENDING}',
  run_id UUID REFERENCES {sp}runs(id) ON DELETE SET NULL,
  site_id TEXT,
  {qual_cols},
  UNIQUE (dedupe_key)
);
CREATE INDEX IF NOT EXISTS qualified_leads_run_id_idx ON {sp}qualified_leads (run_id);
CREATE INDEX IF NOT EXISTS qualified_leads_site_id_idx ON {sp}qualified_leads (site_id);
CREATE INDEX IF NOT EXISTS qualified_leads_review_status_idx ON {sp}qualified_leads (review_status);
""",
    )

    tracker_cols = ",\n  ".join(
        f'{qident(dbmod._tracker_sql_column_name(h))} TEXT' for h in dbmod.TRACKER_CSV_HEADERS
    )
    write(
        MIGRATIONS / "006_tracker_rows.sql",
        f"""
CREATE TABLE IF NOT EXISTS {sp}tracker_rows (
  id BIGSERIAL PRIMARY KEY,
  dedupe_key TEXT NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  run_id UUID REFERENCES {sp}runs(id) ON DELETE SET NULL,
  site_id TEXT,
  {tracker_cols},
  UNIQUE (dedupe_key)
);
CREATE INDEX IF NOT EXISTS tracker_rows_site_id_idx ON {sp}tracker_rows (site_id);
""",
    )


if __name__ == "__main__":
    main()
