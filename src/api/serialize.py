"""Serialize Postgres rows for JSON responses."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def serialize_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return serialize_row(value)
    if isinstance(value, list):
        return [serialize_value(v) for v in value]
    return value


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: serialize_value(val) for key, val in row.items()}


def serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [serialize_row(row) for row in rows]
