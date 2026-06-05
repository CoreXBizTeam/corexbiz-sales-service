"""Durable worker handoff via run spec JSON (no Postgres job queue)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict
from uuid import UUID

# Cloud Run container env total limit is 32 KiB — keep headroom for other vars.
MAX_RUN_SPEC_BYTES = 30_000

REQUIRED_FIELDS = ("id", "site_id", "source_type")


class RunSpecError(ValueError):
    """Invalid or oversized run specification."""


def validate_run_spec(run: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize and validate a run spec before worker handoff."""
    if not isinstance(run, dict):
        raise RunSpecError("run spec must be a JSON object")

    missing = [field for field in REQUIRED_FIELDS if not str(run.get(field) or "").strip()]
    if missing:
        raise RunSpecError(f"run spec missing required fields: {', '.join(missing)}")

    try:
        UUID(str(run["id"]))
    except (TypeError, ValueError) as exc:
        raise RunSpecError("run spec id must be a UUID") from exc

    criteria = run.get("criteria") or {}
    if isinstance(criteria, str):
        try:
            criteria = json.loads(criteria)
        except json.JSONDecodeError as exc:
            raise RunSpecError("run spec criteria must be JSON") from exc
    if not isinstance(criteria, dict):
        raise RunSpecError("run spec criteria must be an object")

    return {
        "id": str(run["id"]),
        "site_id": str(run["site_id"]),
        "site_url": run.get("site_url"),
        "list_name": run.get("list_name"),
        "source_type": str(run["source_type"]),
        "criteria": criteria,
        "notes": str(run.get("notes") or ""),
        "webhook_url": run.get("webhook_url"),
    }


def encode_run_spec(run: Dict[str, Any]) -> str:
    """Compact JSON for SALES_RUN_SPEC env or config files."""
    validated = validate_run_spec(run)
    raw = json.dumps(validated, separators=(",", ":"), sort_keys=True)
    size = len(raw.encode("utf-8"))
    if size > MAX_RUN_SPEC_BYTES:
        raise RunSpecError(
            f"run spec too large for env handoff ({size} bytes; max {MAX_RUN_SPEC_BYTES})"
        )
    return raw


def decode_run_spec(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RunSpecError("run spec is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RunSpecError("run spec must be a JSON object")
    return validate_run_spec(parsed)


def load_run_spec(*, config_path: Path | None = None) -> Dict[str, Any]:
    """Load run spec from --config file or SALES_RUN_SPEC env."""
    if config_path is not None:
        return decode_run_spec(config_path.read_text(encoding="utf-8"))

    raw = (os.getenv("SALES_RUN_SPEC") or "").strip()
    if raw:
        return decode_run_spec(raw)

    raise RunSpecError("run spec not provided (--config or SALES_RUN_SPEC required)")


def write_run_spec_file(run: Dict[str, Any], path: Path) -> Path:
    """Write validated run spec to a JSON file (CLI / debug handoff)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encode_run_spec(run), encoding="utf-8")
    return path
