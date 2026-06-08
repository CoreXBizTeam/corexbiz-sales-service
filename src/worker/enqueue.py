"""Accept lead runs: validate, dispatch workers, return immediately."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.log import get_logger, log_action
from src.worker.job_handoff import RunSpecError, validate_run_spec
from src.worker.run_executor import safe_execute_run

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

logger = get_logger(__name__)


def _test_worker_mode() -> str:
    """Test-only overrides (sync/disabled). Production uses the worker pool."""
    return (os.getenv("SALES_WORKER_MODE") or "pool").strip().lower()


def run_claimed_job(run_id: str) -> None:
    """
    Claim a queued run from Postgres and execute it.

    Used from FastAPI BackgroundTasks so Cloud Run keeps CPU after the 202
    response until the pipeline finishes (daemon worker threads alone are
    throttled once the HTTP request completes).
    """
    from src.db import repository as repo
    from src.db.pool import get_pool

    key = str(run_id).strip()
    if not key:
        return

    pool = get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            row = repo.claim_run_by_id(conn, UUID(key))
    if row is None:
        return

    safe_execute_run(repo.run_row_to_spec(row))


def dispatch_run(
    run: dict[str, Any],
    *,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """
    Start background execution for a run already inserted as queued in Postgres.
    """
    run_id = str(run.get("id") or "")
    mode = _test_worker_mode()
    log_action(
        logger,
        logging.INFO,
        "WORKER",
        f"run/{run_id}",
        {"mode": mode},
        traces=[("dispatch", "run queued in Postgres")],
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
        from src.db import repository as repo
        from src.db.pool import get_pool

        source_type = str(validated.get("source_type") or "")
        with get_pool().connection() as conn:
            with conn.transaction():
                repo.mark_run_running(
                    conn,
                    UUID(run_id),
                    message=f"Running pipeline ({source_type})…",
                )
        safe_execute_run(validated)
        return

    from src.worker.worker_pool import ensure_worker_pool_started

    ensure_worker_pool_started()
    if background_tasks is not None:
        background_tasks.add_task(run_claimed_job, run_id)
        log_action(
            logger,
            logging.INFO,
            "WORKER",
            f"run/{run_id}",
            None,
            traces=[("schedule", "BackgroundTasks execution after 202")],
        )


def enqueue_run(
    run: dict[str, Any],
    *,
    background_tasks: BackgroundTasks | None = None,
) -> int:
    """Backward-compatible alias — run must already exist in Postgres as queued."""
    dispatch_run(run, background_tasks=background_tasks)
    from src.worker import job_queue

    return job_queue.queue_position(str(run.get("id") or ""))
