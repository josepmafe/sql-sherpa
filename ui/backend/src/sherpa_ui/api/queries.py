from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Final

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from sherpa_ui.api.errors import UNKNOWN_COLUMN_CODE, ApiException
from sherpa_ui.readonly.query_runner import QueryOutcome
from sherpa_ui.readonly.sort_filter import DuplicateColumnsError, FilterSpec, SortSpec

if TYPE_CHECKING:
    from sherpa_ui.readonly.query_runner import QueryRunner

logger = logging.getLogger(__name__)

MAX_SQL_CHARS: Final = 100_000  # a request body bound; far above anything typed into the editor
MAX_QUERY_ID_CHARS: Final = 100  # the client sends a UUID
DISCONNECT_POLL_SECONDS: Final = 0.5  # UI spec §4
DUPLICATE_COLUMNS_WARNING: Final = "duplicate column names: sorting and filtering apply only to the rows shown"

router = APIRouter(prefix="/queries", tags=["queries"])


class QueryRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    query_id: str = Field(min_length=1, max_length=MAX_QUERY_ID_CHARS)
    sql: str = Field(max_length=MAX_SQL_CHARS)
    sort: tuple[SortSpec, ...] = ()
    filters: tuple[FilterSpec, ...] = ()


class QueryResponse(QueryOutcome):
    warning: str | None = None


class FormatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str = Field(max_length=MAX_SQL_CHARS)


class FormatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str


class QueryService:
    """The query endpoints' logic. The runner blocks, so every call to it runs in a worker thread."""

    def __init__(self, runner: QueryRunner) -> None:
        self._runner = runner

    async def run(self, request: Request, body: QueryRequest) -> QueryResponse:
        watcher = asyncio.create_task(self._cancel_on_disconnect(request, body.query_id))
        try:
            return await run_in_threadpool(self._run, body)
        finally:
            watcher.cancel()

    async def cancel(self, query_id: str) -> None:
        await run_in_threadpool(self._runner.cancel, query_id)

    async def format(self, body: FormatRequest) -> FormatResponse:
        return FormatResponse(sql=await run_in_threadpool(self._runner.format, body.sql))

    def _run(self, body: QueryRequest) -> QueryResponse:
        try:
            try:
                outcome = self._runner.run(body.query_id, body.sql, body.sort, body.filters)
            except DuplicateColumnsError:
                # The browser sorts and filters what it got; say so instead of failing.
                outcome = self._runner.run(body.query_id, body.sql)
                return QueryResponse(**outcome.model_dump(), warning=DUPLICATE_COLUMNS_WARNING)
        except ValueError as error:  # a sort or filter column that is not in the result
            raise ApiException(422, UNKNOWN_COLUMN_CODE, str(error)) from error

        return QueryResponse(**outcome.model_dump())

    async def _cancel_on_disconnect(self, request: Request, query_id: str) -> None:
        """Starlette keeps running an endpoint whose client left, so poll for it and stop the query."""
        while True:
            await asyncio.sleep(DISCONNECT_POLL_SECONDS)
            if await request.is_disconnected():
                logger.info("client of query %s disconnected; cancelling it", query_id)
                with contextlib.suppress(Exception):  # nobody is left to tell; the run ends on its own anyway
                    await self.cancel(query_id)

                return


@router.post("")
async def run_query(body: QueryRequest, request: Request) -> QueryResponse:
    service: QueryService = request.app.state.query_service
    return await service.run(request, body)


@router.post("/format")
async def format_query(body: FormatRequest, request: Request) -> FormatResponse:
    service: QueryService = request.app.state.query_service
    return await service.format(body)


@router.post("/{query_id}/cancel", status_code=204)
async def cancel_query(query_id: str, request: Request) -> Response:
    service: QueryService = request.app.state.query_service
    await service.cancel(query_id)
    return Response(status_code=204)
