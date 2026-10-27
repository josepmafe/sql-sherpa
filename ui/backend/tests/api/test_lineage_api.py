from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec

import pytest
from sherpa_mcp.catalog import CatalogUnavailable
from sherpa_ui.build.build_status import BuildStatusReader, BuildStatusUnavailable
from sherpa_ui.lineage.lineage_service import LineageService
from sherpa_ui.lineage.manifest_graph import ManifestGraph
from sherpa_ui.lineage.manifest_loader import ManifestLoader
from sherpa_ui.lineage.node_sql import NodeSql
from sherpa_ui.lineage.selector import SUPPORTED_FORMS

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient


@pytest.fixture
def manifest_loader(manifest: dict[str, Any]) -> ManifestLoader:
    fake = create_autospec(ManifestLoader, instance=True)
    fake.load.return_value = ManifestGraph.from_manifest(manifest)
    return fake


@pytest.fixture
def builds() -> BuildStatusReader:
    fake = create_autospec(BuildStatusReader, instance=True)
    fake.last_full_build.side_effect = BuildStatusUnavailable("down")
    return fake


@pytest.fixture
def lineage_client(
    app: FastAPI, client: TestClient, manifest_loader: ManifestLoader, builds: BuildStatusReader
) -> TestClient:
    node_sql = create_autospec(NodeSql, instance=True)
    node_sql.compiled.return_value = None
    app.state.lineage_service = LineageService(manifest_loader, builds, node_sql)
    return client


class TestLineageApi:
    def test_unsupported_selector_is_422_with_the_supported_forms(self, lineage_client: TestClient) -> None:
        response = lineage_client.get("/api/lineage", params={"select": "tag:nightly"})

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "unsupported_selector"
        assert SUPPORTED_FORMS in body["message"]

    def test_unknown_node_is_404(self, lineage_client: TestClient) -> None:
        response = lineage_client.get("/api/lineage/nodes/model.p.nope")

        assert response.status_code == 404
        assert response.json()["code"] == "node_not_found"

    def test_graph_without_a_database_has_no_status(self, lineage_client: TestClient) -> None:
        body = lineage_client.get("/api/lineage", params={"select": "fct_orders"}).json()

        assert body["build"] == {"state": "unavailable", "started_at": None, "node_count": None}
        assert [(node["name"], node["status"]) for node in body["nodes"]] == [("fct_orders", "no_build")]

    def test_layers_filter_drops_edges_to_hidden_nodes(self, lineage_client: TestClient) -> None:
        body = lineage_client.get("/api/lineage", params={"select": "fct_orders+", "layers": "marts,exposure"}).json()

        assert {node["name"] for node in body["nodes"]} == {"fct_orders", "dim_customers", "sales_dashboard"}
        assert {(edge["source"], edge["target"]) for edge in body["edges"]} == {
            ("model.p.fct_orders", "model.p.dim_customers"),
            ("model.p.fct_orders", "exposure.p.sales_dashboard"),
        }
        assert "intermediate" in body["layers_available"]

    def test_unknown_layer_is_invalid_request(self, lineage_client: TestClient) -> None:
        response = lineage_client.get("/api/lineage", params={"layers": "marts,bogus"})

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"
        assert "bogus" in response.json()["message"]

    def test_selector_over_the_cap_is_an_unsupported_selector(self, lineage_client: TestClient) -> None:
        response = lineage_client.get("/api/lineage", params={"select": "a" * 1001})

        assert (response.status_code, response.json()["code"]) == (422, "unsupported_selector")

    def test_failing_only_without_a_build_is_empty(self, lineage_client: TestClient) -> None:
        body = lineage_client.get("/api/lineage", params={"failing_only": "true"}).json()

        assert body["nodes"] == []

    def test_node_panel(self, lineage_client: TestClient) -> None:
        body = lineage_client.get("/api/lineage/nodes/model.p.fct_orders").json()

        assert body["is_mart"] is True
        assert body["raw_sql"] == "select 1 as fct_orders"
        assert body["compiled_sql"] is None
        assert body["sql_note"]

    def test_missing_manifest_is_503(self, lineage_client: TestClient, manifest_loader: ManifestLoader) -> None:
        manifest_loader.load.side_effect = CatalogUnavailable("no manifest")  # type: ignore[attr-defined]

        responses = [lineage_client.get("/api/lineage"), lineage_client.get("/api/lineage/nodes/model.p.fct_orders")]

        assert [response.status_code for response in responses] == [503, 503]
        assert responses[0].json()["code"] == "catalog_unavailable"
