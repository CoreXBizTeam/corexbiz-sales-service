"""Cloud Logging queries for admin /admin/logs on Cloud Run."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.admin.log_buffer import (
    LogRow,
    extract_request_id,
    is_cloud_run_environment,
    query_local_logs,
    sanitize_request_id,
)

MAX_LOG_RESULTS = 100
DEFAULT_SERVICE = "corex-sales-service-dev"


def resolve_cloud_run_service_name() -> str:
    return (
        os.getenv("K_SERVICE")
        or os.getenv("CLOUD_RUN_SERVICE_NAME")
        or os.getenv("CLOUD_RUN_SERVICE")
        or DEFAULT_SERVICE
    )


def sanitize_service_name(name: str) -> str:
    import re

    s = str(name).strip()
    if not s or len(s) > 128 or not re.match(r"^[a-zA-Z0-9_-]+$", s):
        return DEFAULT_SERVICE
    return s


def build_cloud_run_log_filter(service_name: str, request_id: str | None) -> str:
    svc = sanitize_service_name(service_name)
    filt = f'resource.type="cloud_run_revision" AND resource.labels.service_name="{svc}"'
    rid = sanitize_request_id(request_id)
    if rid:
        filt += (
            f' AND (textPayload:"{rid}" OR jsonPayload.request_id="{rid}"'
            f' OR jsonPayload.requestId="{rid}")'
        )
    return filt


def application_default_credentials_likely_present() -> bool:
    if os.getenv("K_SERVICE"):
        return True
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return True
    home = Path.home()
    adc = home / ".config" / "gcloud" / "application_default_credentials.json"
    return adc.is_file()


def _log_entry_to_row(entry: Any) -> LogRow:
    meta = getattr(entry, "metadata", None) or {}
    ts_date = meta.get("timestamp") if isinstance(meta, dict) else getattr(meta, "timestamp", None)
    if hasattr(ts_date, "isoformat"):
        timestamp = ts_date.isoformat()
    else:
        timestamp = str(ts_date or "")

    severity = str(meta.get("severity") if isinstance(meta, dict) else getattr(meta, "severity", "DEFAULT") or "DEFAULT")

    data = getattr(entry, "payload", None)
    if data is None:
        data = getattr(entry, "data", None)

    message = ""
    if isinstance(data, str):
        message = data
    elif data is not None:
        try:
            import json

            message = json.dumps(data, default=str)
        except (TypeError, ValueError):
            message = str(data)

    req_id = extract_request_id(data if isinstance(data, dict) else None, message)
    if len(message) > 16_000:
        message = f"{message[:16_000]}…"

    return LogRow(timestamp=timestamp, severity=severity.upper(), message=message, request_id=req_id)


def query_admin_logs(*, request_id: str | None = None, limit: int = MAX_LOG_RESULTS) -> dict[str, Any]:
    if not is_cloud_run_environment():
        logs = query_local_logs(request_id=request_id, limit=limit)
        return {
            "logs": [row.__dict__ for row in logs],
            "source": "process",
            "hint": (
                "Local run: entries are structured lines from this process (stdout). "
                "Cloud Run uses Cloud Logging instead."
            ),
        }

    if not application_default_credentials_likely_present():
        return {
            "logs": [],
            "error": (
                "Cloud Logging needs Application Default Credentials. "
                "Run: gcloud auth application-default login (or set GOOGLE_APPLICATION_CREDENTIALS). "
                "On Cloud Run, grant this service account roles/logging.viewer."
            ),
        }

    service_name = resolve_cloud_run_service_name()
    filt = build_cloud_run_log_filter(service_name, request_id)

    try:
        from google.cloud import logging as cloud_logging  # type: ignore[attr-defined]

        client = cloud_logging.Client()
        entries = client.list_entries(filter_=filt, max_results=min(limit, MAX_LOG_RESULTS))
        rows = [_log_entry_to_row(entry) for entry in entries]
        return {"logs": [row.__dict__ for row in rows], "source": "cloud_logging"}
    except Exception as exc:
        raw = str(exc)
        message = raw
        if "default credentials" in raw.lower():
            message = (
                "Cloud Logging needs Application Default Credentials. "
                "Run: gcloud auth application-default login. "
                f"Details: {raw}"
            )
        return {"logs": [], "error": message}
