"""Postgres-backed FIFO job queue (runs table, status=queued)."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from src.db import repository as repo
from src.db.pool import get_pool


def queue_position(run_id: str | UUID) -> int:
    """1-based position for an already-inserted queued run."""
    pool = get_pool()
    with pool.connection() as conn:
        return repo.queue_position_for_run(conn, UUID(str(run_id)))


def pending_count() -> int:
    pool = get_pool()
    with pool.connection() as conn:
        return repo.count_queued_runs(conn)


def remove_job(run_id: str | UUID) -> None:
    """Remove a queued run (rollback after failed dispatch)."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            repo.delete_queued_run(conn, UUID(str(run_id)))


def take_job(timeout: float | None = 1.0) -> dict[str, Any] | None:
    """
    Claim the next queued run and return its spec, or None after timeout.

    Uses SELECT … FOR UPDATE SKIP LOCKED so multiple workers/instances can scale.
    """
    deadline = time.time() + (timeout if timeout is not None else 1.0)
    pool = get_pool()
    while time.time() < deadline:
        with pool.connection() as conn:
            with conn.transaction():
                row = repo.claim_next_queued_run(conn)
            if row is not None:
                return repo.run_row_to_spec(row)
        time.sleep(0.15)
    return None


def clear_queue() -> None:
    """Tests only — delete all queued runs."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM runs WHERE status = 'queued'")


# Backward-compatible aliases used by enqueue logging.
def enqueue_job(run_id: str) -> int:
    return queue_position(run_id)
