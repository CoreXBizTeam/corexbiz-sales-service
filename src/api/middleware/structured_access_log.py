"""Share-style http_accept / http_respon access logs for the admin log viewer."""

from __future__ import annotations

import sys

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.admin.log_buffer import record_log_line
from src.log.bracket_access import format_bracket_access_line, level_from_http_status, truncate_snippet
from src.log.context import get_request_id

_SKIP_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})
_SKIP_PREFIXES = ("/admin",)
_MAX_CAPTURE_BYTES = 256_000


def _should_log(path: str) -> bool:
    if path in _SKIP_PATHS:
        return False
    return not any(path.startswith(prefix) for prefix in _SKIP_PREFIXES)


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    record_log_line(line)


class StructuredAccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not _should_log(path):
            return await call_next(request)

        rid = getattr(request.state, "request_id", None) or get_request_id()
        method = request.method

        _emit(
            format_bracket_access_line(
                level="info",
                action="http_accept",
                method=method,
                pathname=path,
                status="--",
                response_snippet="-",
                request_id=rid,
            )
        )

        response = await call_next(request)

        content_length = response.headers.get("content-length")
        try:
            too_large = content_length is not None and int(content_length) > _MAX_CAPTURE_BYTES
        except (TypeError, ValueError):
            too_large = False

        if too_large:
            _emit(
                format_bracket_access_line(
                    level=level_from_http_status(response.status_code),
                    action="http_respon",
                    method=method,
                    pathname=path,
                    status=response.status_code,
                    response_snippet=f"{content_length}b",
                    request_id=rid,
                )
            )
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        snippet = truncate_snippet(body.decode("utf-8", errors="replace") if body else "")

        _emit(
            format_bracket_access_line(
                level=level_from_http_status(response.status_code),
                action="http_respon",
                method=method,
                pathname=path,
                status=response.status_code,
                response_snippet=snippet,
                request_id=rid,
            )
        )

        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
