"""Run lifecycle API routes."""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.auth import require_api_token
from src.api.schemas import (
    ALLOWED_SOURCE_TYPES,
    ActiveRunResponse,
    CreateRunRequest,
    CreateRunResponse,
    RunResponse,
)
from src.api.serialize import serialize_row
from src.config.env import (
    GOOGLE_MAPS_SOURCE_TYPES,
    google_maps_config_error,
    google_maps_configured,
)
from src.db import repository as repo
from src.log import get_logger, log_action, log_run_poll
from src.worker.enqueue import enqueue_run
from src.worker import job_queue, run_registry

router = APIRouter(prefix="/runs", tags=["runs"])
logger = get_logger(__name__)


def _run_response(row: dict) -> RunResponse:
    payload = serialize_row(repo.run_to_status_payload(row))
    return RunResponse(**payload)


def _site_scope(body: CreateRunRequest) -> tuple[str, str]:
    site_id = (body.site_id or "dev-server").strip()
    site_url = (body.site_url or "http://localhost").strip()
    return site_id, site_url


def _active_run_response(row: dict) -> ActiveRunResponse:
    payload = serialize_row(repo.run_to_status_payload(row))
    run_id = payload.get("id") or payload.get("run_id")
    status = str(payload.get("status") or "idle")
    running = bool(payload.get("running"))
    return ActiveRunResponse(
        running=running,
        status=status,
        run_id=run_id,
        list_name=payload.get("list_name"),
        source_type=payload.get("source_type"),
        message=payload.get("message"),
        error=payload.get("error"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
    )


def _idle_active_response() -> ActiveRunResponse:
    return ActiveRunResponse(running=False, status="idle")


def _status_row_for_site(site_id: str) -> dict | None:
    """In-memory only — live status for this API instance while the worker runs."""
    return run_registry.get_active_run_for_site(site_id)


def _request_id(request: Request) -> str | None:
    rid = getattr(request.state, "request_id", None)
    return str(rid).strip() if rid else None


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreateRunResponse)
def create_run(
    body: CreateRunRequest,
    request: Request,
    _: None = Depends(require_api_token),
) -> CreateRunResponse:
    site_id, site_url = _site_scope(body)
    source_type = body.source_type.strip()
    rid = _request_id(request)
    if source_type not in ALLOWED_SOURCE_TYPES:
        log_action(
            logger,
            logging.WARNING,
            "RUN",
            "POST /api/v1/runs",
            {"source_type": source_type, "site_id": site_id},
            traces=[(400, "invalid source_type")],
            request_id=rid,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_source_type",
                "message": f"source_type must be one of {sorted(ALLOWED_SOURCE_TYPES)}",
            },
        )

    if source_type in GOOGLE_MAPS_SOURCE_TYPES and not google_maps_configured():
        log_action(
            logger,
            logging.WARNING,
            "RUN",
            "POST /api/v1/runs",
            {"source_type": source_type, "site_id": site_id},
            traces=[(503, "google_maps_not_configured")],
            request_id=rid,
        )
        raise HTTPException(
            status_code=503,
            detail=google_maps_config_error(),
        )

    run_id = uuid4()
    run_spec = repo.run_spec_from_request(
        run_id=run_id,
        site_id=site_id,
        site_url=site_url,
        list_name=body.list_name.strip() or None,
        source_type=source_type,
        criteria=body.criteria,
        notes=body.notes,
        webhook_url=body.webhook_url or repo.default_webhook_url(site_url),
    )
    run_registry.register_run(run_spec, message="Lead run queued…")
    try:
        queue_position = enqueue_run(run_spec)
    except Exception:
        run_registry.remove_run(run_id)
        job_queue.remove_job(str(run_id))
        raise

    log_action(
        logger,
        logging.INFO,
        "RUN",
        f"run/{run_id}",
        {
            "source_type": source_type,
            "site_id": site_id,
            "list_name": body.list_name.strip() or None,
            "status": "queued",
            "queue_position": queue_position,
        },
        traces=[(202, "run queued")],
        request_id=rid,
    )
    return CreateRunResponse(
        run_id=run_id,
        status="queued",
        started=True,
        queue_position=max(queue_position, 1),
    )


@router.get("/active", response_model=ActiveRunResponse)
def get_active_run_progress(
    request: Request,
    site_id: str = Query(..., min_length=1),
    _: None = Depends(require_api_token),
) -> ActiveRunResponse:
    """Return in-progress run status for a site (in-memory on this API instance)."""
    site_id = site_id.strip()
    rid = _request_id(request)
    row = _status_row_for_site(site_id)
    if row is None:
        return _idle_active_response()

    payload = _active_run_response(row)
    log_run_poll(
        row.get("id") or row.get("run_id"),
        status=payload.status,
        source_type=payload.source_type,
        message=payload.message,
        error=payload.error,
        running=payload.running,
        request_id=rid,
    )
    return payload


@router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id: UUID,
    request: Request,
    site_id: str = Query(..., min_length=1),
    _: None = Depends(require_api_token),
) -> RunResponse:
    site_id = site_id.strip()
    rid = _request_id(request)

    active = run_registry.get_run(run_id)
    if active is not None and active.get("site_id") == site_id:
        payload = serialize_row(repo.run_to_status_payload(active))
        log_run_poll(
            run_id,
            status=str(payload.get("status") or ""),
            source_type=str(payload.get("source_type") or "") or None,
            message=payload.get("message"),
            error=payload.get("error"),
            running=bool(payload.get("running")),
            request_id=rid,
        )
        return RunResponse(**payload)

    from src.db.pool import get_pool

    pool = get_pool()
    with pool.connection() as conn:
        row = repo.get_run_for_site(conn, run_id, site_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    payload = repo.run_to_status_payload(row)
    log_run_poll(
        run_id,
        status=str(payload.get("status") or ""),
        source_type=str(payload.get("source_type") or "") or None,
        message=payload.get("message"),
        error=payload.get("error"),
        running=bool(payload.get("running")),
        request_id=rid,
    )
    return _run_response(row)
