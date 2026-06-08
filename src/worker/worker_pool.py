"""Fixed-size worker pool that executes queued lead runs."""

from __future__ import annotations

import logging
import os
import threading

from src.log import get_logger, log_action
from src.worker import job_queue
from src.worker.run_executor import safe_execute_run

logger = get_logger(__name__)

_pool_lock = threading.Lock()
_pool_started = False


def max_workers() -> int:
    raw = (os.getenv("SALES_WORKER_POOL_SIZE") or "4").strip()
    try:
        size = int(raw)
    except ValueError:
        size = 4
    return max(1, min(size, 16))


def _worker_loop(worker_index: int) -> None:
    while True:
        run = job_queue.take_job(timeout=1.0)
        if run is None:
            continue

        run_id = str(run.get("id") or "")
        source_type = str(run.get("source_type") or "")
        message = f"Running pipeline ({source_type})…"
        run["message"] = message

        from src.db import repository as repo
        from src.db.pool import get_pool
        from uuid import UUID

        with get_pool().connection() as conn:
            with conn.transaction():
                repo.mark_run_running(conn, UUID(run_id), message=message)

        log_action(
            logger,
            logging.INFO,
            "WORKER",
            f"run/{run_id}",
            {"worker": worker_index, "pool_size": max_workers()},
            traces=[("start", "picked up queued run")],
        )
        safe_execute_run(run)


def ensure_worker_pool_started() -> None:
    """Start daemon worker threads once per process."""
    global _pool_started
    with _pool_lock:
        if _pool_started:
            return
        count = max_workers()
        for index in range(count):
            thread = threading.Thread(
                target=_worker_loop,
                args=(index,),
                name=f"sales-worker-{index}",
                daemon=True,
            )
            thread.start()
        _pool_started = True
        log_action(
            logger,
            logging.INFO,
            "WORKER",
            "pool",
            {"workers": count},
            traces=[("start", "worker pool ready")],
        )


def pool_started() -> bool:
    return _pool_started


def reset_pool_for_tests() -> None:
    """Tests only — pool threads are not stopped; clear queued runs separately."""
    global _pool_started
    with _pool_lock:
        _pool_started = False
