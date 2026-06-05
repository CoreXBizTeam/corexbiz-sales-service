#!/usr/bin/env python3
"""
One-time import of legacy ``corex_leads.db`` into Postgres (sales-service schema).

Creates a completed run with ``source_type=legacy_sqlite``, syncs leads +
qualified_leads (including review_status / notes), and optionally dispatches
``run.completed`` webhook so the WordPress plugin pulls into local tables.

Usage:
  source .venv/bin/activate
  python scripts/migrate_legacy_sqlite.py --db ./corex_leads.db
  python scripts/migrate_legacy_sqlite.py --webhook --site-url https://yoursite.test
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
_cloud = os.getenv("CLOUD_SQL_ENV", "/Users/tobymalek/corexbiz/cloud-sql/env.local")
if os.path.isfile(_cloud):
    load_dotenv(_cloud, override=False)

DEFAULT_DB = ROOT / "corex_leads.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy SQLite into Postgres")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Legacy SQLite path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--site-id",
        default=(os.getenv("SALES_SITE_ID") or os.getenv("COREXBIZ_SITE_ID") or "dev-server"),
        help="Site id stored on imported rows (default: SALES_SITE_ID or dev-server)",
    )
    parser.add_argument(
        "--site-url",
        default=os.getenv("SALES_SITE_URL") or os.getenv("WP_SITEURL") or "",
        help="Site URL for webhook auto-resolution (default: SALES_SITE_URL)",
    )
    parser.add_argument(
        "--list-name",
        default="Legacy SQLite import",
        help="Run list_name label",
    )
    parser.add_argument(
        "--webhook",
        action="store_true",
        help="Dispatch run.completed webhook after import (requires WEBHOOK_SIGNING_SECRET)",
    )
    parser.add_argument(
        "--webhook-only",
        metavar="RUN_ID",
        help="Re-dispatch run.completed webhook for an existing Postgres run",
    )
    parser.add_argument(
        "--webhook-url",
        default="",
        help="Override webhook URL (with --webhook-only). Local default: SALES_SITE_URL + /wp-json/...",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate SQLite only; do not write Postgres",
    )
    args = parser.parse_args()

    if args.webhook_only:
        from uuid import UUID

        from src.worker.webhook import notify_run_finished

        run_id = UUID(str(args.webhook_only).strip())
        webhook_url = (getattr(args, "webhook_url", None) or "").strip() or None
        notify_run_finished(
            run_id,
            event="run.completed",
            webhook_url=webhook_url,
        )
        print(f"Webhook dispatch attempted for run {run_id}")
        return

    if not args.db.exists():
        raise SystemExit(f"SQLite database not found: {args.db}")

    import sqlite3

    sqlite_conn = sqlite3.connect(str(args.db))
    try:
        lead_count = sqlite_conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        qualified_count = sqlite_conn.execute(
            "SELECT COUNT(*) FROM qualified_leads"
        ).fetchone()[0]
    except sqlite3.Error as exc:
        raise SystemExit(f"Invalid legacy database: {exc}") from exc
    finally:
        sqlite_conn.close()

    print(
        f"Legacy DB: {args.db} (leads={lead_count}, qualified={qualified_count}, "
        f"site_id={args.site_id})"
    )

    if args.dry_run:
        print("Dry run — no Postgres changes.")
        return

    from src.db.migrate import run_migrations
    from src.db.pool import close_pool, get_pool
    from src.db import repository as repo
    from src.worker.sync_sqlite import sync_sqlite_to_postgres

    run_migrations()
    run_id = uuid.uuid4()
    site_url = (args.site_url or "").strip() or None
    webhook_url = repo.default_webhook_url(site_url)

    pool = get_pool()
    try:
        with pool.connection() as conn:
            with conn.transaction():
                repo.persist_run_result(
                    conn,
                    run_id=run_id,
                    site_id=args.site_id,
                    site_url=site_url,
                    list_name=args.list_name,
                    source_type="legacy_sqlite",
                    criteria={"legacy_db": str(args.db.resolve())},
                    notes="Phase 8 legacy SQLite cutover",
                    webhook_url=webhook_url,
                    status="completed",
                    message="Legacy SQLite import",
                )
                counts = sync_sqlite_to_postgres(
                    conn,
                    args.db,
                    run_id=run_id,
                    site_id=args.site_id,
                )
    finally:
        close_pool()

    print(f"Run {run_id} completed in Postgres: {counts}")

    if args.webhook:
        from src.worker.webhook import notify_run_finished

        notify_run_finished(
            run_id,
            event="run.completed",
            qualified_count=int(counts.get("qualified_leads", 0)),
        )
        print("Webhook dispatch attempted (check logs if plugin did not sync).")
    elif webhook_url:
        print(f"Tip: re-run with --webhook to push run {run_id} to {webhook_url}")


if __name__ == "__main__":
    main()
