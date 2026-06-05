"""Pydantic models for CoreX Sales Service API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ALLOWED_SOURCE_TYPES = frozenset(
    {"google_maps", "google_web", "manual_csv", "custom_script"}
)


class CreateRunRequest(BaseModel):
    list_name: str = Field(default="", max_length=500)
    source_type: str
    criteria: Dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=4000)
    webhook_url: Optional[str] = Field(default=None, max_length=2000)
    site_id: Optional[str] = Field(default=None, max_length=255)
    site_url: Optional[str] = Field(default=None, max_length=2000)


class CreateRunResponse(BaseModel):
    run_id: UUID
    status: str = "queued"
    started: bool = True


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    site_id: str
    site_url: Optional[str] = None
    list_name: Optional[str] = None
    source_type: str
    criteria: Dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    status: str
    error: Optional[str] = None
    message: Optional[str] = None
    webhook_url: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: Optional[str] = None
    running: bool = False


class ActiveRunResponse(BaseModel):
    """Progress snapshot for the current site run (poll while pipeline executes)."""

    running: bool
    status: str = "idle"
    run_id: Optional[UUID] = None
    list_name: Optional[str] = None
    source_type: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class PaginatedLeadsResponse(BaseModel):
    leads: List[Dict[str, Any]]
    count: int
    page: int
    per_page: int
    total: int


class LeadsBundleResponse(BaseModel):
    raw_leads: List[Dict[str, Any]]
    qualified_leads: List[Dict[str, Any]]
    leads: List[Dict[str, Any]]
    tracker_rows: List[Dict[str, Any]]
    exports: List[Dict[str, Any]]
    count: int
    page: int
    per_page: int
    total_qualified: int


class QualifiedLeadPatchRequest(BaseModel):
    review_status: str
    notes: str = ""


class QualifiedLeadPatchResponse(BaseModel):
    id: int
    review_status: str
    notes: str
