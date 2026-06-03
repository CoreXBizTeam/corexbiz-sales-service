"""Apply versioned SQL migrations to the sales-service Postgres schema."""

from __future__ import annotations

import logging
from pathlib import Path

from psycopg import Connection

from src.db.connection import is_auto_seed_enabled, resolve_database_url, resolve_schema_name
from src.db.pool import get_pool

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_migration_files() -> list[tuple[str, str]]:
    if not MIGRATIONS_DIR.is_dir():
        raise RuntimeError(f"Migrations directory not found: {MIGRATIONS_DIR}")
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError(f"No migration files in {MIGRATIONS_DIR}")
    return [(f.name, f.read_text(encoding="utf-8").strip()) for f in files]


def _ensure_migration_table(conn: Connection, schema: str) -> None:
    schema_q = _quote_ident(schema)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_q}")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema_q}.schema_migrations (
          id TEXT PRIMARY KEY,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _run_with_conn(
    conn: Connection,
    schema: str,
    migrations: list[tuple[str, str]],
) -> dict[str, list[str]]:
    applied: list[str] = []
    skipped: list[str] = []

    _ensure_migration_table(conn, schema)
    schema_q = _quote_ident(schema)
    rows = conn.execute(f"SELECT id FROM {schema_q}.schema_migrations ORDER BY id").fetchall()
    applied_set = {r[0] for r in rows}

    for migration_id, sql in migrations:
        if migration_id in applied_set:
            skipped.append(migration_id)
            continue
        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                f"INSERT INTO {schema_q}.schema_migrations (id) VALUES (%s)",
                (migration_id,),
            )
        applied.append(migration_id)
        logger.info("applied migration %s", migration_id)

    if applied:
        logger.info(
            'schema "%s" seeded (%d applied, %d skipped)',
            schema,
            len(applied),
            len(skipped),
        )
    else:
        logger.info('schema "%s" up to date (%d skipped)', schema, len(skipped))

    return {"applied": applied, "skipped": skipped}


def run_migrations(conn: Connection | None = None) -> dict[str, list[str]]:
    """Apply pending migrations. Returns applied and skipped migration ids."""
    if not is_auto_seed_enabled():
        logger.info("DB_AUTO_SEED disabled; skipping migrations")
        return {"applied": [], "skipped": []}

    if not resolve_database_url():
        raise RuntimeError("Schema seed requires DATABASE_URL or POSTGRES_* settings")

    schema = resolve_schema_name()
    migrations = load_migration_files()

    if conn is None:
        pool = get_pool()
        with pool.connection() as active_conn:
            return _run_with_conn(active_conn, schema, migrations)
    return _run_with_conn(conn, schema, migrations)


def prepare_database_if_needed() -> dict[str, list[str]] | None:
    """Run migrations on startup when DATABASE_URL is configured."""
    if not is_auto_seed_enabled():
        return None
    if not resolve_database_url():
        return None
    return run_migrations()
