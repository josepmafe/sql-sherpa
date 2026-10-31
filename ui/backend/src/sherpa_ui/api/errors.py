"""The one error shape of the API, `{code, message, detail?}` (UI spec §4), and the handlers that produce it."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sherpa_mcp.catalog import CatalogUnavailable
from starlette.exceptions import HTTPException as StarletteHTTPException

from sherpa_ui.readonly.query_runner import UiQueryError

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# UI spec §4. Clients branch on the body's `code`; several codes share a status. A timeout is 504, not 408: Chromium
# silently re-sends a POST answered 408 on a reused keep-alive connection, which would run the query twice.
QUERY_ERROR_STATUS: Final[dict[str, int]] = {
    "guard_rejected": 422,
    "sql_error": 422,
    "timeout": 504,
    "duplicate_query_id": 409,
    "cancelled": 409,
    "busy": 429,
    "catalog_unavailable": 503,
    "database_unavailable": 503,
    "too_many_connections": 503,
}
# 422: a request body over the cap, found while it was read (`api/body_limit.py`).
HTTP_STATUS_CODES: Final[dict[int, str]] = {404: "not_found", 405: "method_not_allowed", 422: "invalid_request"}
UNKNOWN_COLUMN_CODE: Final = "unknown_column"  # a sort or filter column that is not in the result
PII_COLUMN_CODE: Final = "pii_column"  # a sort or filter column whose values the masking hides
INVALID_REQUEST_CODE: Final = "invalid_request"
DEFAULT_HTTP_CODE: Final = "http_error"
INTERNAL_ERROR_CODE: Final = "internal_error"
INTERNAL_ERROR_MESSAGE: Final = "something went wrong on the server; see its log"
# The OpenAPI schema's description of each error status, with the codes it carries; every error body is an ApiError.
ERROR_STATUS_DESCRIPTIONS: Final[dict[int, str]] = {
    404: "Not found: `table_not_found`, `node_not_found`, `column_not_found`",
    409: "Conflict with a query of the same id: `duplicate_query_id`, `cancelled`",
    422: (
        "Refused: `invalid_request`, `guard_rejected`, `sql_error`, `unknown_column`, `pii_column`, "
        "`unsupported_selector`"
    ),
    429: "Too many queries are running: `busy`",
    500: "Unexpected server error: `internal_error`",
    503: "A dependency is unavailable: `catalog_unavailable`, `database_unavailable`, `too_many_connections`",
    504: "The query ran past the statement timeout: `timeout`",
}


class ErrorDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: int | None = None  # 0-based character offset in the user's SQL


class ApiError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    detail: ErrorDetail | None = None


class ApiException(Exception):
    """An error with a status and code of its own, answered as an `ApiError`."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = ApiError(code=code, message=message)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiException, _api_exception)  # type: ignore[arg-type]
    app.add_exception_handler(UiQueryError, _query_error)  # type: ignore[arg-type]
    app.add_exception_handler(CatalogUnavailable, _catalog_unavailable)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _invalid_request)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _internal_error)


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The OpenAPI `responses` of a route that answers `statuses` with an `ApiError`. Declaring 422 also keeps FastAPI
    from describing it as its own HTTPValidationError, which `_invalid_request` never sends."""
    responses: dict[int | str, dict[str, Any]] = {
        status: {"model": ApiError, "description": ERROR_STATUS_DESCRIPTIONS[status]} for status in statuses
    }
    return responses


def _respond(status_code: int, error: ApiError) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json", exclude_none=True))


async def _api_exception(_: Request, exc: ApiException) -> JSONResponse:
    return _respond(exc.status_code, exc.error)


async def _query_error(_: Request, exc: UiQueryError) -> JSONResponse:
    detail = ErrorDetail(position=exc.position) if exc.position is not None else None
    return _respond(QUERY_ERROR_STATUS[exc.code], ApiError(code=exc.code, message=exc.message, detail=detail))


async def _catalog_unavailable(_: Request, exc: CatalogUnavailable) -> JSONResponse:
    return _respond(503, ApiError(code="catalog_unavailable", message=str(exc)))


async def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    message = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors())
    return _respond(422, ApiError(code=INVALID_REQUEST_CODE, message=message))


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = HTTP_STATUS_CODES.get(exc.status_code, DEFAULT_HTTP_CODE)
    return _respond(exc.status_code, ApiError(code=code, message=str(exc.detail)))


async def _internal_error(_: Request, exc: Exception) -> JSONResponse:
    """The last resort: whatever no other handler took, so the client still gets the one error shape."""
    logger.error("unhandled error", exc_info=exc)
    return _respond(500, ApiError(code=INTERNAL_ERROR_CODE, message=INTERNAL_ERROR_MESSAGE))
