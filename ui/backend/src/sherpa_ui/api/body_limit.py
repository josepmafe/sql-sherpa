"""A cap on request bodies, enforced while they arrive, before FastAPI reads one whole (UI spec §4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException

from sherpa_ui.api.errors import INVALID_REQUEST_CODE, ApiError

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Far above the largest valid request: a `sql` of 100,000 characters (MAX_SQL_CHARS) is at most 600,000 bytes of
# JSON (a control character escapes to six), plus the sort and filters.
MAX_BODY_BYTES: Final = 1_048_576
BODY_TOO_LARGE_MESSAGE: Final = f"the request body is over {MAX_BODY_BYTES:,} bytes"
BODY_TOO_LARGE_STATUS: Final = 422  # an invalid request, like a `sql` over MAX_SQL_CHARS


class BodySizeLimit:
    """ASGI middleware: a request that declares a Content-Length over `MAX_BODY_BYTES` is answered without reading
    its body, and a body sent without one (chunked) is counted as it arrives and stopped once it passes the cap."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only POST routes read a body (and declare 422); the others never do, so they keep their own statuses.
        if scope["type"] != "http" or scope["method"] != "POST":
            await self._app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            error = ApiError(code=INVALID_REQUEST_CODE, message=BODY_TOO_LARGE_MESSAGE)
            response = JSONResponse(status_code=BODY_TOO_LARGE_STATUS, content=error.model_dump(exclude_none=True))
            await response(scope, receive, send)
            return

        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY_BYTES:
                    # FastAPI re-raises an HTTPException met while reading the body; `_http_error` answers it.
                    raise StarletteHTTPException(BODY_TOO_LARGE_STATUS, BODY_TOO_LARGE_MESSAGE)

            return message

        await self._app(scope, counted, send)
