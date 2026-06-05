"""Leads read/update API routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth import require_api_token
from src.api.schemas import (
    LeadsBundleResponse,
    PaginatedLeadsResponse,
    QualifiedLeadPatchRequest,
    QualifiedLeadPatchResponse,
)
from src.api.serialize import serialize_rows
from src.db.pool import get_pool
from src.db import repository as repo

router = APIRouter(tags=["leads"])


@router.get("/runs/{run_id}/leads", response_model=PaginatedLeadsResponse)
def get_run_qualified_leads(
    run_id: UUID,
    site_id: str = Query(..., min_length=1),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=500),
    _: None = Depends(require_api_token),
) -> PaginatedLeadsResponse:
    site_id = site_id.strip()
    pool = get_pool()
    with pool.connection() as conn:
        run = repo.get_run_for_site(conn, run_id, site_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        leads, total = repo.list_qualified_for_run(
            conn,
            run_id,
            site_id,
            page=page,
            per_page=per_page,
        )
    serialized = serialize_rows(leads)
    return PaginatedLeadsResponse(
        leads=serialized,
        count=len(serialized),
        page=page,
        per_page=per_page,
        total=total,
    )


@router.get("/sites/{site_id}/leads-bundle", response_model=LeadsBundleResponse)
def get_site_leads_bundle(
    site_id: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=500),
    _: None = Depends(require_api_token),
) -> LeadsBundleResponse:
    site_id = site_id.strip()
    pool = get_pool()
    with pool.connection() as conn:
        qualified, total_qualified = repo.list_qualified_for_site(
            conn, site_id, page=page, per_page=per_page
        )
        raw_leads, _ = repo.list_raw_leads_for_site(
            conn, site_id, page=page, per_page=per_page
        )
        tracker_rows = repo.get_all_tracker_rows(conn, site_id=site_id)
        exports = repo.get_recent_exports(conn, limit=5)

    qualified_serialized = serialize_rows(qualified)
    return LeadsBundleResponse(
        raw_leads=serialize_rows(raw_leads),
        qualified_leads=qualified_serialized,
        leads=qualified_serialized,
        tracker_rows=serialize_rows(tracker_rows),
        exports=serialize_rows(exports),
        count=len(qualified_serialized),
        page=page,
        per_page=per_page,
        total_qualified=total_qualified,
    )


@router.patch("/qualified-leads/{lead_id}", response_model=QualifiedLeadPatchResponse)
def patch_qualified_lead(
    lead_id: int,
    body: QualifiedLeadPatchRequest,
    site_id: str = Query(..., min_length=1),
    _: None = Depends(require_api_token),
) -> QualifiedLeadPatchResponse:
    site_id = site_id.strip()
    pool = get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            try:
                updated = repo.update_qualified_review(
                    conn,
                    lead_id,
                    site_id,
                    body.review_status,
                    body.notes,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not updated:
        raise HTTPException(status_code=404, detail="lead not found")
    return QualifiedLeadPatchResponse(**updated)
