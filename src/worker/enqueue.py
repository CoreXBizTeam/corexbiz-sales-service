"""Dispatch pipeline runs in a background thread on the API process."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from src.log import get_logger, log_action
from src.worker.job_handoff import RunSpecError, validate_run_spec

logger = get_logger(__name__)


def _test_worker_mode() -> str:
    """Test-only overrides (sync/disabled). Production always uses inline threads."""
    return (os.getenv("SALES_WORKER_MODE") or "inline").strip().lower()


def _safe_execute(run: dict[str, Any]) -> None:
    from src.worker.run_job import execute_run

    try:
        execute_run(run)
    except Exception as exc:
        log_action(
            logger,
            logging.ERROR,
            "WORKER",
            f"run/{run.get('id')}",
            None,
            traces=[("error", str(exc))],
            exc_info=True,
        )


def enqueue_run(run: dict[str, Any]) -> None:
    """Start the lead pipeline on a daemon thread (same process as the HTTP API)."""
    run_id = run.get("id")
    mode = _test_worker_mode()
    log_action(
        logger,
        logging.INFO,
        "WORKER",
        f"run/{run_id}",
        {"mode": mode},
        traces=[("enqueue", "dispatching pipeline")],
    )

    try:
        validated = validate_run_spec(run)
    except RunSpecError:
        raise

    if mode in ("disabled", "off", "none"):
        log_action(
            logger,
            logging.INFO,
            "WORKER",
            f"run/{run_id}",
            {"mode": mode},
            traces=[("skip", "worker dispatch disabled")],
        )
        return

    if mode == "sync":
        _safe_execute(validated)
        return

    thread = threading.Thread(
        target=_safe_execute,
        args=(validated,),
        name=f"sales-run-{run_id}",
        daemon=True,
    )
    thread.start()
