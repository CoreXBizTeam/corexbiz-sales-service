"""Signed admin session cookie (HMAC)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from src.admin.config import COOKIE_NAME, DEFAULT_TTL_SEC

__all__ = ["COOKIE_NAME", "DEFAULT_TTL_SEC", "create_admin_session", "verify_admin_session", "read_cookie"]


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def create_admin_session(signing_secret: str, ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    exp = int(time.time()) + ttl_sec
    body = _b64url_encode(json.dumps({"exp": exp}, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(signing_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_admin_session(token: str | None, signing_secret: str) -> bool:
    if not token or not signing_secret:
        return False
    parts = str(token).split(".")
    if len(parts) != 2:
        return False
    body, sig = parts
    expected = hmac.new(signing_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        payload: dict[str, Any] = json.loads(_b64url_decode(body).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return False
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return False
    return exp >= int(time.time())


def read_cookie(cookie_header: str | None, name: str = COOKIE_NAME) -> str | None:
    if not cookie_header:
        return None
    prefix = f"{name}="
    for part in cookie_header.split(";"):
        trimmed = part.strip()
        if trimmed.startswith(prefix):
            return trimmed[len(prefix) :]
    return None
