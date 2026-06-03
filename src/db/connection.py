"""Resolve Postgres connection settings from environment."""

from __future__ import annotations

import os
from urllib.parse import quote


def resolve_schema_name() -> str:
    raw = (os.getenv("POSTGRES_SCHEMA") or os.getenv("DATABASE_SCHEMA") or "sales-service").strip()
    return raw or "sales-service"


def resolve_database_url() -> str | None:
    direct = (os.getenv("DATABASE_URL") or "").strip()
    if direct:
        return direct

    host = (os.getenv("POSTGRES_HOST") or os.getenv("PGHOST") or "").strip()
    port = (os.getenv("POSTGRES_PORT") or os.getenv("PGPORT") or "5432").strip()
    database = (os.getenv("POSTGRES_DB") or os.getenv("PGDATABASE") or "").strip()
    user = (os.getenv("POSTGRES_USER") or os.getenv("PGUSER") or "").strip()
    password = os.getenv("POSTGRES_PASSWORD") or os.getenv("PGPASSWORD") or ""
    if not host or not database or not user:
        return None

    sslmode = (os.getenv("POSTGRES_SSLMODE") or "disable").strip()
    user_q = quote(user, safe="")
    pass_q = quote(password, safe="")
    db_q = quote(database, safe="")
    return f"postgresql://{user_q}:{pass_q}@{host}:{port}/{db_q}?sslmode={quote(sslmode, safe='')}"


def is_auto_seed_enabled() -> bool:
    raw = (os.getenv("DB_AUTO_SEED") or "true").strip().lower()
    return raw not in {"0", "false", "no"}
