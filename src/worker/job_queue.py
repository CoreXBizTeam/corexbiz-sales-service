"""FIFO job queue for lead pipeline runs (in-process)."""

from __future__ import annotations

import threading
from collections import deque


class JobQueue:
    """Thread-safe queue of run ids waiting for a worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[str] = deque()
        self._available = threading.Condition(self._lock)

    def enqueue(self, run_id: str) -> int:
        """Append a run id; return 1-based position in the pending queue."""
        key = str(run_id).strip()
        with self._available:
            self._pending.append(key)
            self._available.notify()
            return len(self._pending)

    def take(self, timeout: float | None = 1.0) -> str | None:
        """Block until a run id is available or timeout elapses."""
        with self._available:
            if not self._pending:
                self._available.wait(timeout=timeout)
            if not self._pending:
                return None
            return self._pending.popleft()

    def remove(self, run_id: str) -> None:
        with self._available:
            key = str(run_id)
            try:
                self._pending.remove(key)
            except ValueError:
                pass

    def pending_count(self) -> int:
        with self._available:
            return len(self._pending)

    def clear(self) -> None:
        with self._available:
            self._pending.clear()


_queue = JobQueue()


def enqueue_job(run_id: str) -> int:
    return _queue.enqueue(run_id)


def take_job(timeout: float | None = 1.0) -> str | None:
    return _queue.take(timeout=timeout)


def remove_job(run_id: str) -> None:
    _queue.remove(run_id)


def pending_count() -> int:
    return _queue.pending_count()


def clear_queue() -> None:
    _queue.clear()
