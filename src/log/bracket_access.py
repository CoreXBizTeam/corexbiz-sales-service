"""Single-line bracket access logs (same shape as corex-share-service)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

_SNIPPET_MAX = int(os.getenv("SALES_ACCESS_LOG_BODY_SNIPPET_MAX", "120") or "120")
_SNIPPET_MAX = max(20, min(_SNIPPET_MAX, 500))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\r", " ").replace("\n", " ")).strip()


def truncate_snippet(raw: str) -> str:
    flat = sanitize_one_line(raw)
    if not flat:
        return "-"
    if len(flat) <= _SNIPPET_MAX:
        return flat
    return f"{flat[: _SNIPPET_MAX - 1]}…"


def level_from_http_status(code: int) -> str:
    if code >= 500:
        return "error"
    if code >= 400:
        return "warn"
    return "info"


def format_bracket_access_line(
    *,
    level: str,
    action: str,
    method: str,
    pathname: str,
    status: str | int,
    response_snippet: str | None = None,
    request_id: str | None = None,
    iso_time: str | None = None,
) -> str:
    """Format: [Datetime][Level][Action] METHOD path [status][snippet] rid=id"""
    iso = iso_time or _utc_iso()
    lvl = str(level or "info").lower()
    act = re.sub(r"\s+", "_", str(action or "event"))
    meth = str(method or "?").upper().strip()
    path = str(pathname or "/").strip() or "/"

    if status in ("--", None, ""):
        status_disp = "--"
    else:
        status_disp = str(status)

    snippet_raw = response_snippet if response_snippet not in (None, "") else "-"
    snippet = truncate_snippet(snippet_raw) if snippet_raw != "-" else "-"

    rid = f" rid={request_id.strip()}" if request_id and str(request_id).strip() else ""

    return f"[{iso}][{lvl}][{act}] {meth} {path} [{status_disp}][{snippet}]{rid}"
