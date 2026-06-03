"""Service token auth (mirror corex-share-service /api/v1)."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Request


@dataclass(frozen=True)
class SiteIdentity:
    server_id: str
    site_url: str
    plugin_version: str


def configured_api_token() -> str:
    return (os.getenv("API_TOKEN") or "").strip()


def get_request_token(
    *,
    authorization: Optional[str],
    x_api_token: Optional[str],
) -> Optional[str]:
    auth = (authorization or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if x_api_token:
        return str(x_api_token).strip()
    return None


def timing_safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_api_token(provided: Optional[str]) -> None:
    expected = configured_api_token()
    if not provided:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "missing token"},
        )
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": "server token not configured",
            },
        )
    if not timing_safe_equal(provided, expected):
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "invalid token"},
        )


def site_identity_from_headers(
    *,
    server_id: Optional[str],
    site_url: Optional[str],
    plugin_version: Optional[str],
) -> SiteIdentity:
    """Site scope for multi-tenant runs — optional headers default for local dev."""
    return SiteIdentity(
        server_id=(server_id or "dev-server").strip(),
        site_url=(site_url or "http://localhost").strip(),
        plugin_version=(plugin_version or "0.0.0").strip(),
    )


async def require_site_identity(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-Api-Token"),
    x_corexbiz_server_id: Optional[str] = Header(default=None, alias="X-Corexbiz-Server-Id"),
    x_corexbiz_site_url: Optional[str] = Header(default=None, alias="X-Corexbiz-Site-Url"),
    x_corexbiz_plugin_version: Optional[str] = Header(
        default=None, alias="X-Corexbiz-Plugin-Version"
    ),
) -> SiteIdentity:
    verify_api_token(
        get_request_token(authorization=authorization, x_api_token=x_api_token)
    )
    return site_identity_from_headers(
        server_id=x_corexbiz_server_id,
        site_url=x_corexbiz_site_url,
        plugin_version=x_corexbiz_plugin_version,
    )
