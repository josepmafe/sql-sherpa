from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sherpa_mcp.catalog import CatalogUnavailable

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sherpa_mcp.catalog import MartCatalogLoader

EXPECTED_MART_COUNT = 9


class TestCatalogApi:
    def test_tree_groups_by_domain_shared_last(self, client: TestClient) -> None:
        body = client.get("/api/catalog").json()

        assert body["database"] == "sherpa"
        assert body["schema_name"] == "marts"
        assert [domain["name"] for domain in body["domains"]] == ["customers", "sales", "shared"]
        assert [table["name"] for table in body["domains"][1]["tables"]] == ["dim_products", "fct_orders"]
        assert [table["name"] for table in body["domains"][2]["tables"]] == ["dim_agents", "dim_dates"]

    def test_tree_columns_carry_flags(self, client: TestClient) -> None:
        table = client.get("/api/catalog").json()["domains"][0]["tables"][0]

        assert table["primary_key"] == ["dim_customers_id"]
        assert table["columns"][0] == {
            "name": "dim_customers_id",
            "data_type": "text",
            "description": "dim_customers_id column",
            "is_pii": False,
            "is_primary_key": True,
        }

    def test_missing_artifacts_is_503(self, client: TestClient, loader: MartCatalogLoader) -> None:
        loader.load.side_effect = CatalogUnavailable("dbt artifacts not found")  # type: ignore[attr-defined]

        responses = [client.get("/api/catalog"), client.get("/api/catalog/tables/fct_orders")]

        assert [response.status_code for response in responses] == [503, 503]
        assert responses[0].json() == {"code": "catalog_unavailable", "message": "dbt artifacts not found"}

    def test_table_details_resolve_metric_definitions(self, client: TestClient) -> None:
        body = client.get("/api/catalog/tables/fct_orders").json()

        status = body["columns"][1]
        assert body["tests"] == ["not_null", "unique"]
        assert body["synonyms"] == ["thing"]
        assert status["accepted_values"] == ["a", "b"]
        assert status["metric_refs"] == [{"name": "revenue", "definition": "sum of net item amounts"}]

    def test_table_name_is_case_insensitive(self, client: TestClient) -> None:
        response = client.get("/api/catalog/tables/FCT_ORDERS")

        assert response.status_code == 200
        assert response.json()["name"] == "fct_orders"

    def test_unknown_table_is_404(self, client: TestClient) -> None:
        response = client.get("/api/catalog/tables/raw_orders")

        assert response.status_code == 404
        assert response.json()["code"] == "table_not_found"

    @pytest.mark.warehouse
    def test_real_catalog_has_nine_marts(self, warehouse_client: TestClient) -> None:
        body = warehouse_client.get("/api/catalog").json()

        tables = [table for domain in body["domains"] for table in domain["tables"]]
        assert len(tables) == EXPECTED_MART_COUNT
        assert body["domains"][-1]["name"] == "shared"
