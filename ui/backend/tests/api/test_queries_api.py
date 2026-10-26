from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sherpa_ui.api import queries
from sherpa_ui.api.body_limit import MAX_BODY_BYTES
from sherpa_ui.api.queries import MAX_SQL_CHARS, QueryRequest, QueryService
from sherpa_ui.app import create_app
from sherpa_ui.readonly.query_runner import QueryOutcome, QueryRunner, UiQueryError
from sherpa_ui.readonly.sort_filter import (
    FilterSpec,
    PiiColumnError,
    SortSpec,
    UnknownColumnError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sherpa_mcp.catalog import MartCatalogLoader
    from sherpa_ui.settings import UiSettings

QUERY_ID = "11111111-1111-1111-1111-111111111111"
# An aggregate, so no row comes back before the whole cross join is counted: far longer than any test.
LONG_AGGREGATE = (
    "select count(*) as n from marts.fct_order_items a "
    "cross join marts.fct_order_items b cross join marts.fct_order_items c"
)
CANCEL_WITHIN_SECONDS = 2.0
SERVER_START_SECONDS = 10.0
CLIENT_GIVES_UP_SECONDS = 1.0
SAMPLE_SECONDS = 0.02


def outcome(mode: str = "client", warning: str | None = None) -> QueryOutcome:
    return QueryOutcome(
        query_id=QUERY_ID,
        columns=("n",),
        column_types=("number",),
        rows=((1,),),
        returned_rows=1,
        truncated=False,
        elapsed_ms=3,
        mode=mode,  # type: ignore[arg-type]
        warning=warning,
    )


def body(**extra: object) -> dict[str, object]:
    return {"query_id": QUERY_ID, "sql": "select 1 as n", **extra}


class TestQueriesApi:
    def test_run_returns_outcome_without_warning(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.return_value = outcome()

        response = client.post("/api/queries", json=body())

        assert response.status_code == 200
        assert response.json() == {**outcome().model_dump(mode="json"), "warning": None}
        runner.run.assert_called_once_with(QUERY_ID, "select 1 as n", (), ())

    def test_sort_and_filters_reach_the_runner(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.return_value = outcome("server")

        client.post(
            "/api/queries",
            json=body(
                sort=[{"column": "n", "direction": "desc"}],
                filters=[{"column": "n", "operator": ">", "value": "0"}],
            ),
        )

        runner.run.assert_called_once_with(
            QUERY_ID,
            "select 1 as n",
            (SortSpec(column="n", direction="desc"),),
            (FilterSpec(column="n", operator=">", value="0"),),
        )

    @pytest.mark.parametrize(
        ("code", "status"),
        [
            ("guard_rejected", 422),
            ("sql_error", 422),
            ("timeout", 504),
            ("duplicate_query_id", 409),
            ("cancelled", 409),
            ("busy", 429),
            ("catalog_unavailable", 503),
            ("database_unavailable", 503),
            ("too_many_connections", 503),
        ],
    )
    def test_error_codes_map_to_statuses(self, client: TestClient, runner: MagicMock, code: str, status: int) -> None:
        runner.run.side_effect = UiQueryError(code, "boom")  # type: ignore[arg-type]

        response = client.post("/api/queries", json=body())

        assert response.status_code == status
        assert response.json() == {"code": code, "message": "boom"}

    def test_sql_error_position_is_in_detail(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.side_effect = UiQueryError("sql_error", 'column "x" does not exist', position=7)

        response = client.post("/api/queries", json=body())

        assert response.json()["detail"] == {"position": 7}

    def test_position_zero_is_kept(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.side_effect = UiQueryError("sql_error", "syntax error", position=0)

        assert client.post("/api/queries", json=body()).json()["detail"] == {"position": 0}

    def test_duplicate_query_id_is_409(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.side_effect = UiQueryError("duplicate_query_id", "a query with id x is already running")

        response = client.post("/api/queries", json=body())

        assert response.status_code == 409
        assert response.json()["code"] == "duplicate_query_id"

    def test_duplicate_columns_fall_back_with_warning(self, client: TestClient, runner: MagicMock) -> None:
        warning = "duplicate column names: sorting and filtering apply only to the rows shown"
        runner.run.return_value = outcome(warning=warning)

        response = client.post("/api/queries", json=body(sort=[{"column": "n", "direction": "asc"}]))

        assert response.status_code == 200
        assert (response.json()["mode"], response.json()["warning"]) == ("client", warning)
        runner.run.assert_called_once()  # the runner falls back within the run, under the one query id

    def test_unknown_sort_column_is_422(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.side_effect = UnknownColumnError("column 'zzz' is not a column of the result")

        response = client.post("/api/queries", json=body(sort=[{"column": "zzz", "direction": "asc"}]))

        assert response.status_code == 422
        assert response.json() == {"code": "unknown_column", "message": "column 'zzz' is not a column of the result"}

    def test_a_masked_sort_or_filter_column_is_422_pii_column(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.side_effect = PiiColumnError("column 'email' holds personal data")

        response = client.post(
            "/api/queries", json=body(filters=[{"column": "email", "operator": "contains", "value": "@"}])
        )

        assert response.status_code == 422
        assert response.json() == {"code": "pii_column", "message": "column 'email' holds personal data"}
        runner.run.assert_called_once()  # no fallback to a run in the browser's mode

    @pytest.mark.parametrize("failure", [RecursionError("maximum recursion depth exceeded"), RuntimeError("boom")])
    def test_unexpected_error_is_500_internal_error(
        self, app: FastAPI, runner: MagicMock, caplog: pytest.LogCaptureFixture, failure: Exception
    ) -> None:
        runner.run.side_effect = failure

        with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
            response = client.post("/api/queries", json=body())

        assert response.status_code == 500
        assert response.json() == {
            "code": "internal_error",
            "message": "something went wrong on the server; see its log",
        }
        assert "boom" not in response.text
        assert any(record.exc_info and record.exc_info[1] is failure for record in caplog.records)

    def test_specific_handlers_still_win_over_the_catch_all(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.side_effect = UiQueryError("sql_error", "bad sql", position=3)

        sql_error = client.post("/api/queries", json=body())
        invalid = client.post("/api/queries", json={"query_id": QUERY_ID})
        missing = client.get("/api/nope")

        assert (sql_error.status_code, sql_error.json()["code"]) == (422, "sql_error")
        assert (invalid.status_code, invalid.json()["code"]) == (422, "invalid_request")
        assert (missing.status_code, missing.json()["code"]) == (404, "not_found")

    def test_sql_over_the_limit_is_rejected(self, client: TestClient, runner: MagicMock) -> None:
        response = client.post("/api/queries", json=body(sql="x" * (MAX_SQL_CHARS + 1)))

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"
        runner.run.assert_not_called()

    def test_a_body_over_the_cap_is_refused_before_it_is_read(self, client: TestClient, runner: MagicMock) -> None:
        response = client.post(
            "/api/queries", content=_padded_body(MAX_BODY_BYTES + 1), headers={"content-type": "application/json"}
        )

        assert response.status_code == 422
        assert response.json() == {"code": "invalid_request", "message": "the request body is over 1,048,576 bytes"}
        runner.run.assert_not_called()

    def test_a_streamed_body_over_the_cap_is_refused(self, client: TestClient, runner: MagicMock) -> None:
        def chunks() -> Iterator[bytes]:  # no Content-Length: the body is sent chunked
            yield _padded_body(MAX_BODY_BYTES + 1)

        response = client.post("/api/queries", content=chunks(), headers={"content-type": "application/json"})

        assert response.status_code == 422
        assert response.json() == {"code": "invalid_request", "message": "the request body is over 1,048,576 bytes"}
        runner.run.assert_not_called()

    def test_a_get_with_a_large_body_keeps_its_declared_statuses(self, client: TestClient) -> None:
        response = client.request("GET", "/api/catalog", content=_padded_body(MAX_BODY_BYTES + 1))

        assert response.status_code == 200  # the route never reads a body, so no 422 it does not declare

    def test_a_body_at_the_cap_is_read(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.return_value = outcome()

        response = client.post(
            "/api/queries", content=_padded_body(MAX_BODY_BYTES), headers={"content-type": "application/json"}
        )

        assert response.status_code == 200
        runner.run.assert_called_once()

    @pytest.mark.parametrize("sql", ["select 1 as n\x00; delete from marts.fct_orders", "\x00"])
    def test_sql_with_a_nul_character_is_rejected(self, client: TestClient, runner: MagicMock, sql: str) -> None:
        # libpq ends the query text at a NUL, so Postgres would run less than the guard checked.
        response = client.post("/api/queries", json=body(sql=sql))

        assert response.status_code == 422
        assert response.json() == {
            "code": "invalid_request",
            "message": "body.sql: Value error, the SQL contains a NUL character (\\x00), which Postgres cannot receive",
        }
        runner.run.assert_not_called()

    def test_sql_at_the_limit_is_accepted(self, client: TestClient, runner: MagicMock) -> None:
        runner.run.return_value = outcome()

        response = client.post("/api/queries", json=body(sql="x" * MAX_SQL_CHARS))

        assert response.status_code == 200

    @pytest.mark.parametrize(
        "payload",
        [
            {"sql": "select 1"},
            {"query_id": "", "sql": "select 1"},
            {
                "query_id": QUERY_ID,
                "sql": "select 1",
                "filters": [{"column": "n", "operator": "is_null", "value": "1"}],
            },
            {"query_id": QUERY_ID, "sql": "select 1", "sort": [{"column": "n", "direction": "sideways"}]},
        ],
    )
    def test_malformed_request_is_422_invalid_request(
        self, client: TestClient, runner: MagicMock, payload: dict[str, object]
    ) -> None:
        response = client.post("/api/queries", json=payload)

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"
        runner.run.assert_not_called()

    def test_cancel_unknown_id_is_noop(self, app: FastAPI, settings: UiSettings, loader: MartCatalogLoader) -> None:
        app.state.query_service = QueryService(QueryRunner(settings, loader))  # the real runner: no connection needed

        with TestClient(app, base_url="http://localhost") as client:
            response = client.post("/api/queries/never-ran/cancel")

        assert response.status_code == 204
        assert response.content == b""

    def test_cancel_reaches_the_runner(self, client: TestClient, runner: MagicMock) -> None:
        response = client.post(f"/api/queries/{QUERY_ID}/cancel")

        assert response.status_code == 204
        runner.cancel.assert_called_once_with(QUERY_ID)

    def test_format_returns_formatted_sql(self, client: TestClient, runner: MagicMock) -> None:
        runner.format.return_value = "SELECT\n  1"

        response = client.post("/api/queries/format", json={"sql": "select 1"})

        assert response.json() == {"sql": "SELECT\n  1"}
        runner.run.assert_not_called()

    def test_format_error_is_422(self, client: TestClient, runner: MagicMock) -> None:
        runner.format.side_effect = UiQueryError("sql_error", "could not format the query: bad")

        response = client.post("/api/queries/format", json={"sql": "select ("})

        assert response.status_code == 422
        assert response.json()["code"] == "sql_error"


class TestQueryService:
    def test_disconnect_cancels_the_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(queries, "DISCONNECT_POLL_SECONDS", 0.01)
        runner = MagicMock(spec=QueryRunner)
        request = MagicMock()
        request.is_disconnected = AsyncMock(side_effect=[False, True])

        asyncio.run(QueryService(runner)._cancel_on_disconnect(request, QUERY_ID))

        runner.cancel.assert_called_once_with(QUERY_ID)

    def test_connected_client_is_not_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(queries, "DISCONNECT_POLL_SECONDS", 0.01)
        runner = MagicMock(spec=QueryRunner)
        runner.run.return_value = outcome()
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=False)

        async def scenario() -> None:
            response = await QueryService(runner).run(request, QueryRequest(query_id=QUERY_ID, sql="select 1"))
            await asyncio.sleep(0.05)  # the watcher is gone once the query is
            assert response.warning is None

        asyncio.run(scenario())

        runner.cancel.assert_not_called()


@pytest.fixture
def live_server(warehouse_settings: UiSettings) -> Iterator[tuple[str, FastAPI]]:
    """The real app on a real socket, so that a client can really disconnect."""
    app = create_app(warehouse_settings, static_dir=None)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + SERVER_START_SECONDS
    while not server.started and time.monotonic() < deadline:
        time.sleep(SAMPLE_SECONDS)

    assert server.started
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    thread.join(timeout=SERVER_START_SECONDS)


@pytest.mark.warehouse
class TestQueriesApiWarehouse:
    def test_end_to_end_query(self, warehouse_client: TestClient) -> None:
        response = warehouse_client.post(
            "/api/queries", json={"query_id": QUERY_ID, "sql": "select count(*) as n from marts.dim_customers"}
        )

        assert response.status_code == 200
        result = response.json()
        assert result["columns"] == ["n"]
        assert result["mode"] == "client"
        assert result["returned_rows"] == 1

    def test_disconnect_cancels(self, live_server: tuple[str, FastAPI]) -> None:
        url, app = live_server
        runner: QueryRunner = app.state.runner
        seen_running = threading.Event()
        stop = threading.Event()

        def sample() -> None:
            while not stop.is_set():
                if QUERY_ID in runner.running_query_ids():
                    seen_running.set()

                time.sleep(SAMPLE_SECONDS)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()

        with httpx.Client(base_url=url, timeout=CLIENT_GIVES_UP_SECONDS) as client, pytest.raises(httpx.ReadTimeout):
            client.post("/api/queries", json={"query_id": QUERY_ID, "sql": LONG_AGGREGATE})

        deadline = time.monotonic() + CANCEL_WITHIN_SECONDS
        while runner.running_query_ids() and time.monotonic() < deadline:
            time.sleep(SAMPLE_SECONDS)

        stop.set()
        assert seen_running.is_set()  # the query really was running when the client left
        assert runner.running_query_ids() == frozenset()


def _padded_body(size: int) -> bytes:
    """A valid query request of exactly `size` bytes: JSON padded with trailing whitespace."""
    content = json.dumps(body()).encode()
    return content + b" " * (size - len(content))
