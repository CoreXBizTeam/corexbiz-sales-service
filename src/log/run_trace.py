"""Structured progress logs for lead run lifecycle (/api/v1/runs)."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.log.structured import Trace, log_action

logger = logging.getLogger("sales.run")


def _run_poll_logging_enabled() -> bool:
    return os.getenv("SALES_LOG_RUN_POLLS", "").strip().lower() in ("1", "true", "yes")


def log_run_progress(
    run_id: UUID | str,
    status: str,
    *,
    source_type: str | None = None,
    site_id: str | None = None,
    message: str | None = None,
    error: str | None = None,
    stage: str | None = None,
    data: Mapping[str, Any] | None = None,
    traces: Sequence[Trace] | None = None,
    level: int = logging.INFO,
    request_id: str | None = None,
) -> None:
    """Emit a RUN progress line: run/{id} with status, stage, and optional traces."""
    payload: dict[str, Any] = {"status": status}
    if source_type:
        payload["source_type"] = source_type
    if site_id:
        payload["site_id"] = site_id
    if stage:
        payload["stage"] = stage
    if message:
        payload["message"] = message
    if error:
        payload["error"] = error[:500]
    if data:
        payload.update(dict(data))

    trace_list: list[Trace] = [(status, message or stage or status)]
    if traces:
        trace_list.extend(traces)

    log_action(
        logger,
        level,
        "RUN",
        f"run/{run_id}",
        payload,
        traces=trace_list,
        request_id=request_id,
    )


def log_run_poll(
    run_id: UUID | str,
    *,
    status: str,
    source_type: str | None = None,
    message: str | None = None,
    error: str | None = None,
    running: bool = False,
    request_id: str | None = None,
) -> None:
    """Log GET /api/v1/runs/{id} status checks (verbose when SALES_LOG_RUN_POLLS=1)."""
    in_progress = running or status in ("queued", "running")
    if in_progress or _run_poll_logging_enabled():
        log_run_progress(
            run_id,
            status,
            source_type=source_type,
            message=message,
            error=error,
            stage="poll",
            traces=[("GET", "/api/v1/runs/{id}")],
            request_id=request_id,
        )
    else:
        log_action(
            logger,
            logging.DEBUG,
            "RUN",
            f"run/{run_id}",
            {"status": status, "stage": "poll"},
            traces=[("GET", "/api/v1/runs/{id}")],
            request_id=request_id,
        )
