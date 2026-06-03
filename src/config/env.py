"""Pipeline environment helpers."""

from __future__ import annotations

import os

GOOGLE_MAPS_SOURCE_TYPES = frozenset({"google_maps"})


def google_maps_api_key() -> str:
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
