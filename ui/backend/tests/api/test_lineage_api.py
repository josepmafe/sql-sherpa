from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec

import pytest
from sherpa_mcp.catalog import CatalogUnavailable
from sherpa_ui.build.build_status import BuildStatusReader, BuildStatusUnavailable
from sherpa_ui.lineage.column_lineage import (
    ColumnEdge,
    ColumnInfo,
    ColumnKind,
    ColumnLineage,
    ColumnRef,
    Resolution,
)
from sherpa_ui.lineage.column_lineage_loader import ColumnLineageLoader
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


ENRICHED_ID = ColumnRef(unique_id="model.p.int_orders__enriched", column="id")
FCT_ID = ColumnRef(unique_id="model.p.fct_orders", column="id")


@pytest.fixture
def columns_loader() -> ColumnLineageLoader:
    infos = {
        ref.unique_id: (
            ColumnInfo(ref=ref, kind=ColumnKind.PASSTHROUGH, resolution=Resolution.FULL, notes=(), counts=None),
        )
        for ref in (ENRICHED_ID, FCT_ID)
    }
    fake = create_autospec(ColumnLineageLoader, instance=True)
    fake.load.return_value = ColumnLineage(infos, [ColumnEdge(parent=ENRICHED_ID, child=FCT_ID)], {})
    return fake


@pytest.fixture
def lineage_client(
    app: FastAPI,
    client: TestClient,
    manifest_loader: ManifestLoader,
    builds: BuildStatusReader,
    columns_loader: ColumnLineageLoader,
) -> TestClient:
    node_sql = create_autospec(NodeSql, instance=True)
    node_sql.compiled.return_value = None
    app.state.lineage_service = LineageService(manifest_loader, builds, node_sql, columns_loader)
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

    def test_columns_flag_adds_columns(self, lineage_client: TestClient) -> None:
        plain = lineage_client.get("/api/lineage", params={"select": "fct_orders"}).json()
        flagged = lineage_client.get("/api/lineage", params={"select": "fct_orders", "columns": "true"}).json()

        assert plain["nodes"][0]["columns"] is None
        assert flagged["column_lineage"] == "ok"
        assert flagged["nodes"][0]["columns"] == [{"name": "id", "kind": "passthrough", "resolution": "full"}]

    def test_unavailable_column_lineage_still_answers_the_graph(
        self, lineage_client: TestClient, columns_loader: ColumnLineageLoader
    ) -> None:
        columns_loader.load.side_effect = CatalogUnavailable("no catalog")  # type: ignore[attr-defined]

        response = lineage_client.get("/api/lineage", params={"select": "fct_orders", "columns": "true"})

        assert response.status_code == 200
        assert response.json()["column_lineage"] == "unavailable"

    def test_column_focus(self, lineage_client: TestClient) -> None:
        response = lineage_client.get("/api/lineage/columns/model.p.fct_orders/id")

        assert response.status_code == 200
        body = response.json()
        assert body["focus"] == {"node": "model.p.fct_orders", "column": "id"}
        assert [(c["node"], c["direction"]) for c in body["columns"]] == [
            ("model.p.int_orders__enriched", "upstream"),
            ("model.p.fct_orders", "focus"),
        ]
        assert body["edges"] == [
            {
                "parent": {"node": "model.p.int_orders__enriched", "column": "id"},
                "child": {"node": "model.p.fct_orders", "column": "id"},
            }
        ]

    @pytest.mark.parametrize("path", ["model.p.fct_orders/nope", "model.p.nope/id"])
    def test_unknown_column_is_404(self, lineage_client: TestClient, path: str) -> None:
        response = lineage_client.get(f"/api/lineage/columns/{path}")

        assert response.status_code == 404
        assert response.json()["code"] == "column_not_found"

    def test_missing_catalog_is_503_for_the_focus(
        self, lineage_client: TestClient, columns_loader: ColumnLineageLoader
    ) -> None:
        columns_loader.load.side_effect = CatalogUnavailable("no catalog")  # type: ignore[attr-defined]

        response = lineage_client.get("/api/lineage/columns/model.p.fct_orders/id")

        assert (response.status_code, response.json()["code"]) == (503, "catalog_unavailable")
