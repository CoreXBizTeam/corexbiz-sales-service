"""Trigger Cloud Run Job executions for pipeline runs."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from uuid import UUID

import httpx

from src.worker.job_handoff import RunSpecError, encode_run_spec, validate_run_spec

logger = logging.getLogger(__name__)

RUN_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


def cloud_run_job_name() -> Optional[str]:
    return (os.getenv("SALES_CLOUD_RUN_JOB") or os.getenv("CLOUD_RUN_JOB_NAME") or "").strip() or None


def cloud_run_job_configured() -> bool:
    return cloud_run_job_name() is not None


def _project_id() -> Optional[str]:
    for key in ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def _region() -> str:
    return (os.getenv("GCP_REGION") or os.getenv("CLOUD_RUN_REGION") or "us-west1").strip()


def _access_token() -> str:
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as exc:
        raise RuntimeError(
            "google-auth is required for Cloud Run Job dispatch; "
            "install requirements-service.txt"
        ) from exc

    credentials, _project = google.auth.default(scopes=RUN_SCOPES)
    credentials.refresh(google.auth.transport.requests.Request())
    token = getattr(credentials, "token", None)
    if not token:
        raise RuntimeError("failed to obtain GCP access token for Cloud Run Jobs API")
    return str(token)


def build_run_job_request(run_spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build RunJob API body with SALES_RUN_SPEC env override (payload handoff)."""
    validated = validate_run_spec(run_spec)
    encoded = encode_run_spec(validated)
    return {
        "overrides": {
            "containerOverrides": [
                {
                    "args": ["-m", "src.worker.run_job"],
                    "env": [{"name": "SALES_RUN_SPEC", "value": encoded}],
                }
            ]
        }
    }


def dispatch_cloud_run_job(
    run_spec: Dict[str, Any],
    *,
    timeout_sec: float = 30.0,
) -> Dict[str, Any]:
    """
    POST .../jobs/{name}:run to start a pipeline worker execution.

    The full run spec is passed via SALES_RUN_SPEC — no Postgres queue lookup.
    """
    validated = validate_run_spec(run_spec)
    run_id = UUID(str(validated["id"]))

    job_name = cloud_run_job_name()
    project = _project_id()
    region = _region()
    if not job_name or not project:
        raise RuntimeError(
            "Cloud Run Job dispatch is not configured "
            "(set SALES_CLOUD_RUN_JOB and GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT)"
        )

    job_resource = f"projects/{project}/locations/{region}/jobs/{job_name}"
    url = f"https://run.googleapis.com/v2/{job_resource}:run"
    body = build_run_job_request(validated)
    token = _access_token()

    logger.info("dispatching Cloud Run Job %s for run %s", job_resource, run_id)
    with httpx.Client(timeout=timeout_sec) as client:
        response = client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code >= 400:
        detail = (response.text or "")[:1000]
        raise RuntimeError(
            f"Cloud Run Job execute failed ({response.status_code}): {detail}"
        )

    payload = response.json()
    if not isinstance(payload, dict):
        return {"ok": True, "run_id": str(run_id), "handoff": "SALES_RUN_SPEC"}
    return {
        "ok": True,
        "run_id": str(run_id),
        "handoff": "SALES_RUN_SPEC",
        "execution": payload.get("name"),
    }
