"""Admin auth dependency and handlers."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from src.admin.config import AdminAuthConfig, COOKIE_NAME, load_admin_auth_config
from src.admin.session import create_admin_session, read_cookie, verify_admin_session


class LoginBody(BaseModel):
    password: str = ""


def _timing_safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def get_admin_config() -> AdminAuthConfig:
    return load_admin_auth_config()


def require_admin(
    request: Request,
    config: Annotated[AdminAuthConfig, Depends(get_admin_config)],
) -> None:
    if not config.auth_required:
        return
    cookie = read_cookie(request.headers.get("cookie"), COOKIE_NAME)
    if verify_admin_session(cookie, config.signing_secret):
        return
    raise HTTPException(
        status_code=401,
        detail={"error": "admin_unauthorized", "message": "Admin login required"},
    )


def handle_session(config: AdminAuthConfig, cookie: str | None) -> dict[str, object]:
    authenticated = not config.auth_required or verify_admin_session(cookie, config.signing_secret)
    return {"authRequired": config.auth_required, "authenticated": authenticated}


def handle_login(config: AdminAuthConfig, body: LoginBody, response: Response) -> dict[str, object]:
    if not config.auth_required:
        return {"ok": True, "authRequired": False}

    if not body.password:
        raise HTTPException(status_code=400, detail={"error": "missing_password"})

    if not _timing_safe_equal(body.password, config.password):
        raise HTTPException(status_code=401, detail={"error": "invalid_password"})

    token = create_admin_session(config.signing_secret, config.ttl_sec)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/admin",
        max_age=config.ttl_sec,
    )
    return {"ok": True, "authRequired": True}


def handle_logout(config: AdminAuthConfig, response: Response) -> dict[str, object]:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/admin",
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
    )
    return {"ok": True}
