"""HMAC signing for sales run webhooks (mirror WP SalesRunWebhookSignature)."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Dict

REQUEST_PATH = "/wp-json/corexbiz/v1/sales/run-webhook"
PAYLOAD_SEPARATOR = "\n"
CLOCK_SKEW_SEC = 300


def derive_secret(signing_secret: str, server_id: str) -> str:
    material = f"corexbiz-sales-run:{signing_secret}:{server_id}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def sign_payload(
    signing_secret: str,
    *,
    server_id: str,
    raw_body: str,
    timestamp_sec: int | None = None,
) -> Dict[str, str]:
    ts = str(int(timestamp_sec if timestamp_sec is not None else time.time()))
    secret_hex = derive_secret(signing_secret, server_id)
    body_hash = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
    payload = f"{ts}{PAYLOAD_SEPARATOR}{server_id}{PAYLOAD_SEPARATOR}{REQUEST_PATH}{PAYLOAD_SEPARATOR}{body_hash}"
    signature = hmac.new(
        secret_hex.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "X-Corexbiz-Server-Id": server_id,
        "X-Corexbiz-Timestamp": ts,
        "X-Corexbiz-Signature": signature,
    }
