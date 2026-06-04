"""Admin auth configuration from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.config.env import load_project_env

DEFAULT_TTL_SEC = 8 * 60 * 60
COOKIE_NAME = "cbz_sales_admin"


@dataclass(frozen=True)
class AdminAuthConfig:
    disabled: bool
    password: str
    password_configured: bool
    signing_secret: str
    ttl_sec: int
    cookie_secure: bool

    @property
    def auth_required(self) -> bool:
        return not self.disabled and self.password_configured


def load_admin_auth_config() -> AdminAuthConfig:
    load_project_env()
    disabled = os.getenv("ADMIN_AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes")
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    signing_secret = (
        os.getenv("ADMIN_SESSION_SECRET", "").strip()
        or os.getenv("API_TOKEN", "").strip()
        or "corex-sales-admin-dev-only"
    )
    try:
        ttl_sec = int(os.getenv("ADMIN_SESSION_TTL_SEC", str(DEFAULT_TTL_SEC)))
    except ValueError:
        ttl_sec = DEFAULT_TTL_SEC
    if ttl_sec <= 60:
        ttl_sec = DEFAULT_TTL_SEC

    env_label = os.getenv("COREX_SALES_SERVICE_ENV", os.getenv("NODE_ENV", "")).strip().lower()
    cookie_secure = env_label in ("prod", "production") or os.getenv(
        "ADMIN_COOKIE_SECURE", ""
    ).strip().lower() in ("1", "true", "yes")

    return AdminAuthConfig(
        disabled=disabled,
        password=password,
        password_configured=bool(password),
        signing_secret=signing_secret,
        ttl_sec=ttl_sec,
        cookie_secure=cookie_secure,
    )
