"""Accept lead runs: validate, enqueue, return immediately (workers execute async)."""

from __future__ import annotations

import logging
import os
from typing import Any

from src.log import get_logger, log_action
from src.worker import job_queue, run_registry
from src.worker.job_handoff import RunSpecError, validate_run_spec
from src.worker.run_executor import safe_execute_run

logger = get_logger(__name__)


def _test_worker_mode() -> str:
    """Test-only overrides (sync/disabled). Production uses the worker pool."""
    return (os.getenv("SALES_WORKER_MODE") or "pool").strip().lower()


def enqueue_run(run: dict[str, Any]) -> int:
    """
    Queue a validated run for background execution.

    Returns 1-based queue position (0 when sync/disabled test modes).
    """
    run_id = str(run.get("id") or "")
    mode = _test_worker_mode()
    log_action(
        logger,
        logging.INFO,
        "WORKER",
        f"run/{run_id}",
        {"mode": mode},
        traces=[("enqueue", "accepting run")],
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
        return 0

    if mode == "sync":
        source_type = str(validated.get("source_type") or "")
        run_registry.mark_run_running(
            run_id,
            message=f"Running pipeline ({source_type})…",
        )
        safe_execute_run(validated)
        return 0

    from src.worker.worker_pool import ensure_worker_pool_started

    ensure_worker_pool_started()
    position = job_queue.enqueue_job(run_id)
    log_action(
        logger,
        logging.INFO,
        "WORKER",
        f"run/{run_id}",
        {"queue_position": position, "pending": job_queue.pending_count()},
        traces=[("queued", "waiting for worker")],
    )
    return position
