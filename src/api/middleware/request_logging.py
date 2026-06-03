"""Log inbound HTTP requests and responses."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.log import get_logger, log_action

logger = get_logger("sales.http")

_SKIP_BODY_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


async def _request_payload(request: Request) -> dict[str, Any]:
    payload: dict[str, Any] = {"method": request.method}
    if request.query_params:
        payload["query"] = dict(request.query_params)

    client = request.client.host if request.client else None
    if client:
        payload["client"] = client

    if request.method in ("POST", "PUT", "PATCH") and request.url.path not in _SKIP_BODY_PATHS:
        try:
            raw = await request.body()
            if raw:
                ctype = request.headers.get("content-type", "")
                if "json" in ctype:
                    body = json.loads(raw.decode("utf-8"))
                    payload["body"] = body
                else:
                    payload["body_bytes"] = len(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload["body"] = "<unparseable>"

    return payload


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        path = request.url.path
        payload = await _request_payload(request)

        log_action(
            logger,
            logging.INFO,
            "HTTP",
            f"{request.method} {path}",
            payload,
            traces=[("start", "request received")],
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            log_action(
                logger,
                logging.ERROR,
                "HTTP",
                f"{request.method} {path}",
                payload,
                traces=[
                    (500, f"unhandled error after {elapsed_ms}ms"),
                    ("error", str(exc)),
                ],
                exc_info=True,
            )
            raise

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_action(
            logger,
            logging.INFO,
            "HTTP",
            f"{request.method} {path}",
            {"status": response.status_code, "duration_ms": elapsed_ms},
            traces=[(response.status_code, f"completed in {elapsed_ms}ms")],
        )
        return response
