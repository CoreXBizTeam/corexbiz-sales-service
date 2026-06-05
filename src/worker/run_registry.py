"""In-process run tracking — liveness lives in memory, not Postgres."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunRegistry:
    """Thread-safe registry of runs accepted by this API process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}
        # Job-mode progress tracking (non-blocking slot after worker dispatch).
        self._in_flight: dict[str, dict[str, Any]] = {}

    def register(self, run: dict[str, Any]) -> None:
        run_id = str(run["id"])
        with self._lock:
            self._runs[run_id] = {
                **run,
                "id": run_id,
                "status": "queued",
                "error": None,
                "message": None,
                "started_at": None,
                "finished_at": None,
                "created_at": _utcnow(),
            }

    def set_running(self, run_id: UUID | str, *, message: str | None = None) -> None:
        key = str(run_id)
        with self._lock:
            row = self._runs.get(key)
            if not row:
                return
            row["status"] = "running"
            row["started_at"] = row.get("started_at") or _utcnow()
            if message:
                row["message"] = message

    def get(self, run_id: UUID | str) -> dict[str, Any] | None:
        key = str(run_id)
        with self._lock:
            row = self._runs.get(key)
            return dict(row) if row else None

    def get_active_for_site(self, site_id: str) -> dict[str, Any] | None:
        with self._lock:
            for row in self._runs.values():
                if row.get("site_id") != site_id:
                    continue
                if row.get("status") in ("queued", "running"):
                    return dict(row)
        return None

    def track_in_flight(self, run: dict[str, Any]) -> None:
        """Remember a dispatched run for progress polling (job mode; does not block 409 slot)."""
        run_id = str(run["id"])
        with self._lock:
            self._in_flight[run_id] = {
                **run,
                "id": run_id,
                "status": "dispatched",
                "error": None,
                "message": run.get("message") or "Lead run dispatched to worker…",
                "started_at": run.get("started_at") or _utcnow(),
                "finished_at": None,
                "created_at": _utcnow(),
            }

    def get_in_flight(self, run_id: UUID | str) -> dict[str, Any] | None:
        key = str(run_id)
        with self._lock:
            row = self._in_flight.get(key)
            return dict(row) if row else None

    def get_in_flight_for_site(self, site_id: str) -> dict[str, Any] | None:
        with self._lock:
            for row in self._in_flight.values():
                if row.get("site_id") == site_id:
                    return dict(row)
        return None

    def clear_in_flight(self, run_id: UUID | str) -> None:
        with self._lock:
            self._in_flight.pop(str(run_id), None)

    def remove(self, run_id: UUID | str) -> None:
        with self._lock:
            self._runs.pop(str(run_id), None)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._runs.values()]

    def list_in_flight(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._in_flight.values()]

    def list_all_tracked(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(row) for row in self._runs.values()]
            rows.extend(dict(row) for row in self._in_flight.values())
        return rows

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._in_flight.clear()


_registry = RunRegistry()


def register_run(run: dict[str, Any]) -> None:
    _registry.register(run)


def mark_run_running(run_id: UUID | str, *, message: str | None = None) -> None:
    _registry.set_running(run_id, message=message)


def get_run(run_id: UUID | str) -> dict[str, Any] | None:
    return _registry.get(run_id)


def get_active_run_for_site(site_id: str) -> dict[str, Any] | None:
    return _registry.get_active_for_site(site_id)


def track_in_flight_run(run: dict[str, Any]) -> None:
    _registry.track_in_flight(run)


def get_in_flight_run(run_id: UUID | str) -> dict[str, Any] | None:
    return _registry.get_in_flight(run_id)


def get_in_flight_run_for_site(site_id: str) -> dict[str, Any] | None:
    return _registry.get_in_flight_for_site(site_id)


def clear_in_flight_run(run_id: UUID | str) -> None:
    _registry.clear_in_flight(run_id)


def remove_run(run_id: UUID | str) -> None:
    _registry.remove(run_id)


def list_runs() -> list[dict[str, Any]]:
    return _registry.list_runs()


def list_in_flight_runs() -> list[dict[str, Any]]:
    return _registry.list_in_flight()


def list_all_tracked_runs() -> list[dict[str, Any]]:
    return _registry.list_all_tracked()


def clear_runs() -> None:
    _registry.clear()
