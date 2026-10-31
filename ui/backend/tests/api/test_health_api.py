from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import psycopg
import pytest
from sherpa_mcp.catalog import CatalogUnavailable

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sherpa_mcp.catalog import MartCatalogLoader


@pytest.mark.usefixtures("offline_settings")  # the app is built from these settings, and no connection works
class TestHealthApi:
    def test_artifacts_missing_reported(self, client: TestClient, loader: MartCatalogLoader) -> None:
        loader.load.side_effect = CatalogUnavailable("not built")  # type: ignore[attr-defined]

        body = client.get("/api/health").json()

        assert body["artifacts"] == {"status": "missing", "generated_at": None}

    def test_artifacts_ok_reports_generation_time(self, client: TestClient) -> None:
        body = client.get("/api/health").json()

        assert body["artifacts"]["status"] == "ok"
        assert body["artifacts"]["generated_at"].startswith("2026-10-01T00:00:00")

    def test_unreachable_database_reported_down(self, client: TestClient) -> None:
        body = client.get("/api/health").json()

        assert body["database"] == "down"

    def test_a_database_that_refuses_more_connections_is_up(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Another client of the UI's login holds every connection its limit allows: Postgres answered, so the database
        # is up, and a query says `too_many_connections` itself.
        refusal = (
            'connection failed: connection to server at "127.0.0.1", port 5432 failed: '
            'FATAL:  too many connections for role "sherpa_ui"'
        )

        def refused(*args: object, **kwargs: object) -> None:
            raise psycopg.OperationalError(refusal)

        monkeypatch.setattr(psycopg, "connect", refused)

        with caplog.at_level(logging.WARNING, logger="sherpa_ui.health.health_service"):
            body = client.get("/api/health").json()

        assert body["database"] == "ok"
        assert [record.getMessage() for record in caplog.records] == [
            f"health: the database is up but refuses more connections for the UI's login: {refusal}"
        ]


@pytest.mark.warehouse
class TestHealthApiWarehouse:
    def test_the_real_warehouse_and_artifacts_are_up(self, warehouse_client: TestClient) -> None:
        response = warehouse_client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert body["database"] == "ok"
        assert body["artifacts"]["status"] == "ok"
        assert body["artifacts"]["generated_at"] is not None
