"""POST signed run-completion webhooks to the WordPress plugin."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict
from uuid import UUID

import httpx

from src.api.serialize import serialize_row, serialize_value
from src.lib.sales_run_webhook_sign import sign_payload
from src.log import get_logger, log_action

logger = get_logger(__name__)

WEBHOOK_TIMEOUT_SEC = float(os.getenv("SALES_WEBHOOK_TIMEOUT_SEC", "15"))
WEBHOOK_RETRY_ATTEMPTS = max(1, int(os.getenv("SALES_WEBHOOK_RETRY_ATTEMPTS", "3")))
WEBHOOK_RETRY_DELAY_SEC = max(0.0, float(os.getenv("SALES_WEBHOOK_RETRY_DELAY_SEC", "2")))


def _webhook_signing_secret() -> str:
    return (os.getenv("WEBHOOK_SIGNING_SECRET") or "").strip()


def _is_local_env() -> bool:
    return os.getenv("COREX_SALES_SERVICE_ENV", "local").strip().lower() == "local"


def resolve_webhook_url(run: Dict[str, Any], *, override: str | None = None) -> str:
    """
    Pick the webhook target URL.

    Local dev: prefer SALES_SITE_URL (current tunnel) over the URL stored on the run,
    so a restarted cloudflared tunnel still works when the run completes.
    """
    explicit = (override or "").strip()
    if explicit:
        return explicit

    stored = (run.get("webhook_url") or "").strip()
    if not _is_local_env():
        return stored

    site_url = (os.getenv("SALES_SITE_URL") or "").strip()
    if not site_url:
        site_url = str(run.get("site_url") or "").strip()

    from src.db import repository as repo

    refreshed = repo.default_webhook_url(site_url)
    if refreshed:
        if stored and refreshed != stored:
            log_action(
                logger,
                logging.INFO,
                "WEBHOOK",
                f"run/{run.get('id') or ''}",
                {"stored_url": stored, "dispatch_url": refreshed},
                traces=[("refresh", "using SALES_SITE_URL for local webhook dispatch")],
            )
        return refreshed

    return stored


def build_run_webhook_body(
    run: Dict[str, Any],
    *,
    event: str,
    qualified_count: int = 0,
) -> Dict[str, Any]:
    status = str(run.get("status") or "")
    return {
        "event": event,
        "run_id": str(run.get("id") or ""),
        "site_id": str(run.get("site_id") or ""),
        "status": status,
        "list_name": run.get("list_name"),
        "source_type": run.get("source_type"),
        "error": run.get("error"),
        "message": run.get("message"),
        "qualified_count": qualified_count,
        "started_at": serialize_value(run.get("started_at")),
        "finished_at": serialize_value(run.get("finished_at")),
    }


def dispatch_run_webhook(
    run: Dict[str, Any],
    *,
    event: str,
    qualified_count: int = 0,
    webhook_url: str | None = None,
) -> bool:
    """
    POST a signed webhook to the plugin. Returns True when HTTP 2xx.

    Does not raise — logs failures so run status is not rolled back.
    """
    run_id = str(run.get("id") or "")
    url = resolve_webhook_url(run, override=webhook_url)
    if not url:
        log_action(
            logger,
            logging.INFO,
            "WEBHOOK",
            f"run/{run_id}",
            None,
            traces=[("skip", "no webhook_url")],
        )
        return False

    secret = _webhook_signing_secret()
    if not secret:
        log_action(
            logger,
            logging.WARNING,
            "WEBHOOK",
            f"run/{run_id}",
            None,
            traces=[("skip", "WEBHOOK_SIGNING_SECRET not set")],
        )
        return False

    site_id = str(run.get("site_id") or "").strip()
    if not site_id:
        log_action(
            logger,
            logging.WARNING,
            "WEBHOOK",
            f"run/{run_id}",
            None,
            traces=[("skip", "missing site_id")],
        )
        return False

    body_obj = build_run_webhook_body(run, event=event, qualified_count=qualified_count)
    raw_body = json.dumps(body_obj, separators=(",", ":"), sort_keys=True)
    headers = sign_payload(secret, server_id=site_id, raw_body=raw_body)
    headers["Content-Type"] = "application/json"

    log_action(
        logger,
        logging.INFO,
        "WEBHOOK",
        url,
        {"run_id": run_id, "event": event, "qualified_count": qualified_count},
        traces=[("post", "dispatching signed webhook")],
    )

    last_error = ""
    for attempt in range(1, WEBHOOK_RETRY_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=WEBHOOK_TIMEOUT_SEC) as client:
                response = client.post(url, content=raw_body, headers=headers)
        except httpx.HTTPError as exc:
            last_error = str(exc)
            log_action(
                logger,
                logging.WARNING,
                "WEBHOOK",
                url,
                {"run_id": run_id, "attempt": attempt, "max_attempts": WEBHOOK_RETRY_ATTEMPTS},
                traces=[("error", last_error)],
            )
            if attempt < WEBHOOK_RETRY_ATTEMPTS and WEBHOOK_RETRY_DELAY_SEC > 0:
                time.sleep(WEBHOOK_RETRY_DELAY_SEC * attempt)
            continue

        if response.status_code >= 400:
            log_action(
                logger,
                logging.WARNING,
                "WEBHOOK",
                url,
                {"run_id": run_id, "attempt": attempt},
                traces=[
                    (
                        response.status_code,
                        (response.text or "")[:500] or "webhook rejected",
                    ),
                ],
            )
            return False

        log_action(
            logger,
            logging.INFO,
            "WEBHOOK",
            url,
            {"run_id": run_id, "event": event, "attempt": attempt},
            traces=[(response.status_code, "webhook delivered")],
        )
        return True

    if last_error:
        log_action(
            logger,
            logging.WARNING,
            "WEBHOOK",
            url,
            {"run_id": run_id},
            traces=[("failed", last_error)],
        )
    return False


def notify_run_finished(
    run_id: UUID,
    *,
    event: str,
    qualified_count: int = 0,
    webhook_url: str | None = None,
) -> None:
    """Load run, dispatch webhook, mark webhook_sent_at on success."""
    from src.db.pool import get_pool
    from src.db import repository as repo

    pool = get_pool()
    with pool.connection() as conn:
        run = repo.get_run(conn, run_id)
        if not run:
            return
        if qualified_count <= 0:
            qualified_count = repo.count_qualified_for_run(conn, run_id)
        run_payload = serialize_row(run)
        if dispatch_run_webhook(
            run_payload,
            event=event,
            qualified_count=qualified_count,
            webhook_url=webhook_url,
        ):
            with conn.transaction():
                repo.mark_webhook_sent(conn, run_id)
