"""Run lifecycle API routes."""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.auth import SiteIdentity, require_site_identity
from src.api.schemas import ALLOWED_SOURCE_TYPES, CreateRunRequest, CreateRunResponse, RunResponse
from src.api.serialize import serialize_row
from src.config.env import (
    GOOGLE_MAPS_SOURCE_TYPES,
    google_maps_config_error,
    google_maps_configured,
)
from src.db import repository as repo
from src.log import get_logger, log_action, log_run_poll
from src.worker.enqueue import enqueue_run
from src.worker import run_registry

router = APIRouter(prefix="/runs", tags=["runs"])
logger = get_logger(__name__)


def _run_response(row: dict) -> RunResponse:
    payload = serialize_row(repo.run_to_status_payload(row))
    return RunResponse(**payload)


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreateRunResponse)
def create_run(
    body: CreateRunRequest,
    identity: SiteIdentity = Depends(require_site_identity),
) -> CreateRunResponse:
    source_type = body.source_type.strip()
    if source_type not in ALLOWED_SOURCE_TYPES:
        log_action(
            logger,
            logging.WARNING,
            "RUN",
            "POST /api/v1/runs",
            {"source_type": source_type, "site_id": identity.server_id},
            traces=[(400, "invalid source_type")],
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
            {"source_type": source_type, "site_id": identity.server_id},
            traces=[(503, "google_maps_not_configured")],
        )
        raise HTTPException(
            status_code=503,
            detail=google_maps_config_error(),
        )

    active = run_registry.get_active_run_for_site(identity.server_id)
    if active is not None:
        log_action(
            logger,
            logging.INFO,
            "RUN",
            f"run/{active['id']}",
            {"site_id": identity.server_id},
            traces=[(409, "run already in progress")],
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_running",
                "message": "A lead run is already in progress.",
                "run_id": str(active["id"]),
            },
        )

    run_id = uuid4()
    run_spec = repo.run_spec_from_request(
        run_id=run_id,
        site_id=identity.server_id,
        site_url=identity.site_url,
        list_name=body.list_name.strip() or None,
        source_type=source_type,
        criteria=body.criteria,
        notes=body.notes,
        webhook_url=body.webhook_url or repo.default_webhook_url(identity.site_url),
    )
    run_registry.register_run(run_spec)
    enqueue_run(run_spec)
    log_action(
        logger,
        logging.INFO,
        "RUN",
        f"run/{run_id}",
        {
            "source_type": source_type,
            "site_id": identity.server_id,
            "list_name": body.list_name.strip() or None,
            "status": "queued",
        },
        traces=[(202, "worker enqueued")],
    )
    return CreateRunResponse(run_id=run_id, status="queued", started=True)


@router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id: UUID,
    identity: SiteIdentity = Depends(require_site_identity),
) -> RunResponse:
    active = run_registry.get_run(run_id)
    if active is not None and active.get("site_id") == identity.server_id:
        payload = serialize_row(repo.run_to_status_payload(active))
        log_run_poll(
            run_id,
            status=str(payload.get("status") or ""),
            source_type=str(payload.get("source_type") or "") or None,
            message=payload.get("message"),
            error=payload.get("error"),
            running=bool(payload.get("running")),
        )
        return RunResponse(**payload)

    from src.db.pool import get_pool

    pool = get_pool()
    with pool.connection() as conn:
        row = repo.get_run_for_site(conn, run_id, identity.server_id)
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
    )
    return _run_response(row)
