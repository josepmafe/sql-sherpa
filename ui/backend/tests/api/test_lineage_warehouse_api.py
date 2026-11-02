from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sherpa_ui.settings import UiSettings

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


class TestTestDetailsWarehouse:
    def test_every_test_of_the_graph_resolves_with_its_kind_s_definition(
        self, warehouse_client: TestClient, warehouse_settings: UiSettings
    ) -> None:
        manifest = json.loads((warehouse_settings.ui_dbt_target_path / "manifest.json").read_text(encoding="utf-8"))
        ids = [uid for uid, raw in manifest["nodes"].items() if raw["resource_type"] == "test"] + list(
            manifest["unit_tests"]
        )

        problems = []
        for unique_id in ids:
            response = warehouse_client.get(f"/api/lineage/tests/{unique_id}")
            body = response.json()
            if response.status_code != 200:
                problems.append((unique_id, response.status_code))
                continue

            has = {key: bool(body[key]) for key in ("raw_sql", "compiled_sql", "definition")}
            expected = {
                "singular": {"raw_sql": True, "compiled_sql": True, "definition": False},
                "generic": {"raw_sql": False, "compiled_sql": True, "definition": True},
                "unit": {"raw_sql": False, "compiled_sql": False, "definition": True},
            }[body["kind"]]
            if has != expected or body["sql_note"] is not None:
                problems.append((unique_id, has, body["sql_note"]))

        assert len(ids) > 300
        assert problems == []

    def test_a_node_s_tests_carry_ids_that_resolve(self, warehouse_client: TestClient) -> None:
        tests = warehouse_client.get("/api/lineage/nodes/model.sherpa.fct_orders").json()["tests"]

        statuses = {warehouse_client.get(f"/api/lineage/tests/{test['unique_id']}").status_code for test in tests}

        assert tests
        assert statuses == {200}
