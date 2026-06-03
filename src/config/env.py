"""Pipeline environment helpers."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_env_loaded = False

GOOGLE_MAPS_SOURCE_TYPES = frozenset({"google_maps"})


def load_project_env() -> None:
    """Load `.env` (and optional cloud-sql env) into os.environ."""
    global _env_loaded
    if _env_loaded:
        return
    if os.getenv("SALES_DISABLE_DOTENV", "").strip().lower() in ("1", "true", "yes"):
        _env_loaded = True
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _env_loaded = True
        return

    # Project `.env` wins over empty shell exports (common local dev footgun).
    load_dotenv(ROOT / ".env", override=True)
    cloud = os.getenv("CLOUD_SQL_ENV", "/Users/tobymalek/corexbiz/cloud-sql/env.local")
    if os.path.isfile(cloud):
        load_dotenv(cloud, override=False)
    _env_loaded = True


def subprocess_environ() -> dict[str, str]:
    """Environment dict for pipeline subprocesses (finder, qualifier, etc.)."""
    load_project_env()
    return os.environ.copy()


def google_maps_api_key() -> str:
    load_project_env()
    return (os.getenv("GOOGLE_MAPS_API_KEY") or "").strip()


def google_maps_configured() -> bool:
    return bool(google_maps_api_key())


def google_maps_config_error() -> dict[str, str]:
    return {
        "error": "google_maps_not_configured",
        "message": (
            "GOOGLE_MAPS_API_KEY is not set on the sales service. "
            "Add a Google Cloud API key with Places API enabled to "
            "corex-sales-python/.env, then restart the service."
        ),
    }
