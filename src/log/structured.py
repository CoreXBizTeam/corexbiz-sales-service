"""Structured stdout logging for corex-sales-service.

Format::

    [Datetime] [LEVEL] [Action] <url> <data>
      - [code] <trace message>

Example::

    [2026-06-01T12:00:00+00:00] [INFO] [HTTP] POST /api/v1/runs {"source_type":"google_maps"}
      - [202] completed in 42ms
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, Tuple, Union

from src.log.context import get_request_id

Trace = Tuple[Union[str, int], str]

_SENSITIVE_KEY = re.compile(
    r"(password|secret|token|authorization|api[_-]?key|credential)",
    re.I,
)
_MAX_DATA_LEN = 2000
_MAX_TRACE_LEN = 4000
_CONFIGURED = False


def _utc_timestamp(record: logging.LogRecord) -> str:
    dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f"{dt.strftime('%z')[:3]}:{dt.strftime('%z')[3:]}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def sanitize_value(value: Any) -> Any:
    """Redact secrets from log payloads."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, val in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY.search(key_str):
                out[key_str] = "***"
            else:
                out[key_str] = sanitize_value(val)
        return out
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item) for item in value]
    return value


def format_data(data: Any) -> str:
    if data is None or data == "" or data == {}:
        return ""
    if isinstance(data, str):
        return _truncate(data, _MAX_DATA_LEN)
    try:
        cleaned = sanitize_value(data)
        return _truncate(
            json.dumps(cleaned, default=str, separators=(",", ":"), ensure_ascii=False),
            _MAX_DATA_LEN,
        )
    except (TypeError, ValueError):
        return _truncate(str(data), _MAX_DATA_LEN)


class StructuredFormatter(logging.Formatter):
    """Human-readable structured log lines with optional trace blocks."""

    def format(self, record: logging.LogRecord) -> str:
        ts = _utc_timestamp(record)
        level = record.levelname
        action = getattr(record, "action", None)
        if not action:
            if record.name.startswith("uvicorn.access"):
                action = "ACCESS"
            elif record.name.startswith("uvicorn"):
                action = "UVICORN"
            else:
                action = record.name.split(".")[-1].upper()
        url = getattr(record, "url", "") or ""
        data = format_data(getattr(record, "data", None))

        parts = [f"[{ts}]", f"[{level}]", f"[{action}]"]
        if url:
            parts.append(url)
        if data:
            parts.append(data)
        if not url and not data and record.getMessage():
            parts.append(record.getMessage())

        lines = [" ".join(parts)]

        traces: Sequence[Trace] = getattr(record, "traces", ()) or ()
        for code, message in traces:
            msg = _truncate(str(message).replace("\n", " ").strip(), _MAX_TRACE_LEN)
            if msg:
                lines.append(f"  - [{code}] {msg}")

        if record.exc_info:
            exc_text = self.formatException(record.exc_info).strip()
            for exc_line in exc_text.splitlines():
                lines.append(f"  - [trace] {exc_line}")

        rid = getattr(record, "request_id", None) or get_request_id()
        if rid:
            lines[0] = f"{lines[0]} rid={rid}"

        result = "\n".join(lines)
        try:
            from src.admin.log_buffer import record_log_line

            record_log_line(result)
        except Exception:
            pass
        return result


def configure_logging(*, force: bool = False) -> None:
    """Configure root logging once (stdout, structured formatter)."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    if os.getenv("SALES_LOG_STRUCTURED", "1").strip().lower() in ("0", "false", "no"):
        if not _CONFIGURED:
            logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
            _CONFIGURED = True
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    root = logging.getLogger()
    if force:
        root.handlers.clear()
    elif root.handlers:
        for existing in root.handlers:
            if isinstance(existing.formatter, StructuredFormatter):
                _CONFIGURED = True
                return
        root.handlers.clear()

    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def log_action(
    logger: logging.Logger,
    level: int,
    action: str,
    url: str = "",
    data: Any = None,
    *,
    traces: Sequence[Trace] | None = None,
    exc_info: bool = False,
    request_id: str | None = None,
) -> None:
    """Emit one structured log record."""
    rid = request_id or get_request_id()
    logger.log(
        level,
        action,
        extra={
            "action": action,
            "url": url,
            "data": data,
            "traces": tuple(traces or ()),
            "request_id": rid,
        },
        exc_info=exc_info,
    )
