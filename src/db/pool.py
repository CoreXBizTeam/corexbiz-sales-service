"""Shared Postgres connection pool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from psycopg_pool import ConnectionPool

from src.db.connection import resolve_database_url, resolve_schema_name

if TYPE_CHECKING:
    from psycopg import Connection

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool

    url = resolve_database_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL or POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER must be set for Postgres"
        )

    schema = resolve_schema_name()
    _pool = ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=8,
        kwargs={"options": f'-c search_path="{schema}",public'},
        open=True,
    )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def check_connection() -> dict[str, object]:
    """Ping Postgres; used by /health."""
    pool = get_pool()
    with pool.connection() as conn:
        conn.execute("SELECT 1")
        schema = resolve_schema_name()
        row = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = %s)",
            (schema,),
        ).fetchone()
        return {
            "ok": True,
            "schema": schema,
            "schema_exists": bool(row[0]) if row else False,
        }
