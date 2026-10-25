from __future__ import annotations

from typing import TYPE_CHECKING

from sherpa_mcp.catalog import CatalogUnavailable

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sherpa_mcp.catalog import MartCatalogLoader


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
        # The settings point at port 1, where nothing listens.
        body = client.get("/api/health").json()

        assert body["database"] == "down"
