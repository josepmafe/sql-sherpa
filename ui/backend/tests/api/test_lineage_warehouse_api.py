from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.warehouse

SNAPSHOT_ID = "snapshot.sherpa.snap_product_variants"


class TestLineageWarehouse:
    def test_whole_project_has_a_mart_with_its_sources_upstream(self, warehouse_client: TestClient) -> None:
        body = warehouse_client.get("/api/lineage").json()

        layers = {node["layer"] for node in body["nodes"]}
        assert {"source", "staging", "marts"} <= layers
        assert body["build"]["state"] in {"ok", "no_build"}
        by_id = {node["unique_id"]: node for node in body["nodes"]}
        assert by_id["model.sherpa.fct_orders"]["layer"] == "marts"
        assert by_id["model.sherpa.fct_orders"]["domain"] == "sales"
        parents = {edge["source"] for edge in body["edges"] if edge["target"] == "model.sherpa.fct_orders"}
        assert parents
        upstream = warehouse_client.get("/api/lineage", params={"select": "+fct_orders"}).json()
        assert any(node["resource_type"] == "source" for node in upstream["nodes"])

    def test_node_panel_has_compiled_sql_for_fct_orders(self, warehouse_client: TestClient) -> None:
        body = warehouse_client.get("/api/lineage/nodes/model.sherpa.fct_orders").json()

        assert body["is_mart"] is True
        assert body["raw_sql"]
        assert body["compiled_sql"]
        assert "{{" not in body["compiled_sql"]
        assert body["sql_note"] is None
        assert body["columns"]
        assert body["tests"]

    def test_a_snapshot_defined_in_yaml_has_compiled_sql(self, warehouse_client: TestClient) -> None:
        body = warehouse_client.get(f"/api/lineage/nodes/{SNAPSHOT_ID}").json()

        assert body["layer"] == "snapshot"
        assert body["compiled_sql"]
        assert body["sql_note"] is None

    def test_ephemeral_model_is_inlined(self, warehouse_client: TestClient) -> None:
        graph = warehouse_client.get("/api/lineage").json()
        if graph["build"]["state"] != "ok":
            pytest.skip("needs a recorded full prod build (make build-prod)")

        body = warehouse_client.get("/api/lineage/nodes/model.sherpa.int_orders__payments_summarized").json()

        assert body["materialization"] == "ephemeral"
        assert body["status"] == "inlined"
        assert body["build"] is None
