"""Request ID middleware — propagates X-Request-Id for log tracing."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.log.context import set_request_id

_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(_HEADER, "").strip()
        rid = incoming if incoming and len(incoming) <= 128 else uuid.uuid4().hex[:16]
        request.state.request_id = rid
        set_request_id(rid)
        try:
            response = await call_next(request)
        finally:
            set_request_id(None)

        response.headers[_HEADER] = rid
        return response
