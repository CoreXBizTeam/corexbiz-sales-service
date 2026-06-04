"""In-memory log ring buffer for local /admin/logs (Cloud Run uses Cloud Logging)."""

from __future__ import annotations

import json
import os
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

MAX_ENTRIES = 2000
MAX_MESSAGE_LEN = 16_000
_RID_RE = re.compile(r"\s+rid=([a-zA-Z0-9_-]{1,128})(?:\s|$)")
_RID_JSON_RE = re.compile(r'"request_id"\s*:\s*"([^"]+)"')


@dataclass(frozen=True)
class LogRow:
    timestamp: str
    severity: str
    message: str
    request_id: str


_buf: deque[tuple[datetime, str]] = deque(maxlen=MAX_ENTRIES)


def is_cloud_run_environment() -> bool:
    return bool(os.getenv("K_SERVICE"))


def record_log_line(line: str) -> None:
    if is_cloud_run_environment():
        return
    raw = str(line).strip()
    if not raw:
        return
    _buf.appendleft((datetime.now(timezone.utc), raw))


def sanitize_request_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or len(s) > 128:
        return None
    if not re.match(r"^[a-zA-Z0-9_-]+$", s):
        return None
    return s


def extract_request_id(data: Any, text: str) -> str:
    if isinstance(data, dict):
        for key in ("request_id", "requestId"):
            val = data.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key in ("request_id", "requestId"):
                val = parsed.get(key)
                if val is not None and str(val).strip():
                    return str(val).strip()
    except (json.JSONDecodeError, TypeError):
        pass
    m = _RID_JSON_RE.search(text)
    if m:
        return m.group(1)
    m = _RID_RE.search(text)
    if m:
        return m.group(1)
    return ""


def query_local_logs(*, request_id: str | None = None, limit: int = 100) -> list[LogRow]:
    rid = sanitize_request_id(request_id)
    cap = max(1, min(int(limit) if limit else 100, 500))
    out: list[LogRow] = []

    for recorded_at, raw in _buf:
        if len(out) >= cap:
            break
        data: Any = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None

        req_id = extract_request_id(data, raw)
        if rid and req_id != rid and rid not in raw:
            continue

        severity = "DEFAULT"
        if isinstance(data, dict):
            level = data.get("level") or data.get("severity")
            if level is not None:
                severity = str(level).upper()
        else:
            bm = re.search(r"\]\[(INFO|WARN|WARNING|ERROR|DEBUG)\]\[", raw, re.I)
            if bm:
                severity = bm.group(1).upper()

        message = raw if len(raw) <= MAX_MESSAGE_LEN else f"{raw[: MAX_MESSAGE_LEN - 1]}…"
        out.append(
            LogRow(
                timestamp=recorded_at.isoformat(),
                severity=severity,
                message=message,
                request_id=req_id,
            )
        )
    return out


def clear_log_buffer_for_tests() -> None:
    _buf.clear()
