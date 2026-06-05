"""Admin UI routes — login, logs, service overview."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from src.admin.auth import (
    LoginBody,
    get_admin_config,
    handle_login,
    handle_logout,
    handle_session,
    require_admin,
)
from src.admin.cloud_logs import query_admin_logs
from src.admin.config import AdminAuthConfig
from src.admin.session import COOKIE_NAME, read_cookie
from src.config.env import google_maps_configured
from src.db.connection import resolve_database_url
from src.worker import job_queue, run_registry
from src.worker.worker_pool import max_workers, pool_started

_ADMIN_DIR = Path(__file__).resolve().parents[3] / "public" / "admin"

router = APIRouter(tags=["admin"])


def _admin_env_label() -> str:
    return os.getenv("COREX_SALES_SERVICE_ENV", "local")


def _serialize_run(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in row.items():
        if isinstance(val, datetime):
            out[key] = val.isoformat()
        else:
            out[key] = val
    return out


@router.get("/admin")
def admin_index() -> FileResponse:
    return FileResponse(_ADMIN_DIR / "index.html")


@router.get("/admin/app.js")
def admin_app_js() -> FileResponse:
    return FileResponse(_ADMIN_DIR / "app.js", media_type="application/javascript")


@router.get("/admin/api/session")
def admin_session(
    request: Request,
    config: Annotated[AdminAuthConfig, Depends(get_admin_config)],
) -> dict[str, object]:
    cookie = read_cookie(request.headers.get("cookie"), COOKIE_NAME)
    return handle_session(config, cookie)


@router.post("/admin/api/login")
def admin_login(
    body: LoginBody,
    response: Response,
    config: Annotated[AdminAuthConfig, Depends(get_admin_config)],
) -> dict[str, object]:
    return handle_login(config, body, response)


@router.post("/admin/api/logout")
def admin_logout(
    response: Response,
    config: Annotated[AdminAuthConfig, Depends(get_admin_config)],
) -> dict[str, object]:
    return handle_logout(config, response)


@router.get("/admin/api", dependencies=[Depends(require_admin)])
def admin_overview() -> dict[str, Any]:
    db_url = resolve_database_url()
    db_status: dict[str, Any] = {"configured": bool(db_url)}
    if db_url:
        try:
            from src.db.pool import check_connection

            db_status.update(check_connection())
        except Exception as exc:
            db_status["ok"] = False
            db_status["error"] = str(exc)

    return {
        "environment": _admin_env_label(),
        "service": "corex-sales-service",
        "worker_pool": {
            "size": max_workers(),
            "started": pool_started(),
            "pending_jobs": job_queue.pending_count(),
        },
        "database": db_status,
        "google_maps": {"configured": google_maps_configured()},
        "api_docs": "/docs",
        "health": "/health",
    }


@router.get("/admin/api/runs", dependencies=[Depends(require_admin)])
def admin_active_runs() -> dict[str, Any]:
    runs = [_serialize_run(row) for row in run_registry.list_runs()]
    return {"runs": runs, "count": len(runs)}


@router.get("/admin/logs", dependencies=[Depends(require_admin)])
def admin_logs(
    request_id: Annotated[str | None, Query(alias="request_id")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> JSONResponse:
    payload = query_admin_logs(request_id=request_id, limit=limit)
    status = 503 if payload.get("error") and not payload.get("logs") else 200
    return JSONResponse(content=payload, status_code=status)
