from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from sherpa_mcp.catalog import CatalogUnavailable, MartCatalog, MartCatalogLoader

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def target(tmp_path: Path) -> Path:
    shutil.copy(FIXTURES / "manifest.json", tmp_path)
    shutil.copy(FIXTURES / "catalog.json", tmp_path)
    return tmp_path


@pytest.fixture
def catalog(target: Path) -> MartCatalog:
    return MartCatalogLoader(target).load()


def _edit_manifest(target: Path, edit: Any) -> None:
    path = target / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    edit(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


class TestMartCatalogLoader:
    def test_loads_only_marts_schema_models(self, catalog: MartCatalog) -> None:
        assert sorted(mart.name for mart in catalog.marts) == ["dim_customers", "fct_orders"]

    def test_column_types_meta_accepted_values_metric_refs_and_tests(self, catalog: MartCatalog) -> None:
        orders = catalog.mart("fct_orders")
        customers = catalog.mart("dim_customers")

        assert orders is not None
        assert customers is not None
        assert orders.domain == "sales"
        assert orders.grain == "one row per order"
        assert orders.primary_key == ("order_id",)
        assert customers.primary_key == ("customer_id",)
        assert orders.synonyms == ("sales", "purchases")
        assert customers.synonyms == ()
        assert [column.name for column in orders.columns] == [
            "order_id",
            "customer_id",
            "order_date",
            "net_revenue",
            "current_status",
        ]
        revenue = orders.column("net_revenue")
        status = orders.column("current_status")
        assert revenue is not None
        assert status is not None
        assert revenue.data_type == "numeric(12,2)"
        assert revenue.synonyms == ("revenue", "net sales")
        assert revenue.metric_refs == ("net_revenue",)
        assert revenue.accepted_values is None
        assert status.accepted_values == ("placed", "shipped", "cancelled")
        assert status.metric_refs == ()
        assert orders.column("order_id").is_primary_key  # type: ignore[union-attr]
        assert not orders.column("customer_id").is_primary_key  # type: ignore[union-attr]
        assert customers.column("email").is_pii  # type: ignore[union-attr]
        assert not customers.column("customer_id").is_pii  # type: ignore[union-attr]
        assert customers.column("customer_segment").synonyms == ("tier", "segment")  # type: ignore[union-attr]
        # A relationships test names both models; it counts for each of them.
        assert orders.tests == (
            "accepted_values_fct_orders_current_status__placed__shipped__cancelled",
            "not_null_fct_orders_order_id",
            "relationships_fct_orders_customer_id__customer_id__ref_dim_customers_",
        )
        assert customers.tests == (
            "relationships_fct_orders_customer_id__customer_id__ref_dim_customers_",
            "unique_dim_customers_customer_id",
        )

    def test_metrics_from_doc_blocks(self, catalog: MartCatalog) -> None:
        assert [metric.name for metric in catalog.metrics] == ["net_revenue"]
        assert catalog.metrics[0].definition.startswith("Net revenue: gross_revenue")

    def test_generated_at_comes_from_the_manifest(self, catalog: MartCatalog) -> None:
        assert catalog.generated_at.isoformat() == "2026-10-24T12:36:42.863017+00:00"

    def test_public_access_rule_applies_once_any_model_is_public(self, target: Path) -> None:
        def make_orders_public(manifest: dict[str, Any]) -> None:
            manifest["nodes"]["model.sherpa.fct_orders"]["access"] = "public"

        _edit_manifest(target, make_orders_public)

        loaded = MartCatalogLoader(target).load()

        assert loaded.visible_names() == frozenset({"fct_orders"})

    def test_reloads_on_mtime_change(self, target: Path) -> None:
        loader = MartCatalogLoader(target)
        first = loader.load()

        assert loader.load() is first

        def make_orders_public(manifest: dict[str, Any]) -> None:
            manifest["nodes"]["model.sherpa.fct_orders"]["access"] = "public"

        _edit_manifest(target, make_orders_public)
        stat = (target / "manifest.json").stat()
        os.utime(target / "manifest.json", ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        reloaded = loader.load()

        assert reloaded is not first
        assert reloaded.visible_names() == frozenset({"fct_orders"})

    def test_reloads_when_only_catalog_json_changes(self, target: Path) -> None:
        loader = MartCatalogLoader(target)
        first = loader.load()

        stat = (target / "catalog.json").stat()
        os.utime(target / "catalog.json", ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

        assert loader.load() is not first

    def test_missing_artifacts_raise_catalog_unavailable(
        self, target: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        missing_folder = tmp_path_factory.mktemp("empty") / "nope"
        (target / "catalog.json").unlink()

        with pytest.raises(CatalogUnavailable, match="make build-prod") as no_folder:
            MartCatalogLoader(missing_folder).load()
        with pytest.raises(CatalogUnavailable, match="make build-prod") as no_catalog:
            MartCatalogLoader(target).load()

        assert "make docs-prod" not in str(no_folder.value)
        assert "catalog.json" in str(no_catalog.value)

    def test_unparsable_artifact_raises_catalog_unavailable(self, target: Path) -> None:
        (target / "manifest.json").write_text("{not json", encoding="utf-8")

        with pytest.raises(CatalogUnavailable, match="make build-prod"):
            MartCatalogLoader(target).load()

    def test_artifacts_removed_after_a_load_raise_catalog_unavailable(self, target: Path) -> None:
        loader = MartCatalogLoader(target)
        loader.load()
        (target / "manifest.json").unlink()

        with pytest.raises(CatalogUnavailable, match="make build-prod"):
            loader.load()


class TestMartCatalog:
    def test_lookup_is_case_insensitive(self, catalog: MartCatalog) -> None:
        orders = catalog.mart("FCT_Orders")

        assert orders is not None
        assert orders.name == "fct_orders"
        assert orders.column("NET_Revenue") is not None
        assert catalog.mart("stg_shop__customers") is None
        assert orders.column("nope") is None

    def test_pii_columns(self, catalog: MartCatalog) -> None:
        assert catalog.pii_columns() == frozenset({"email"})

    def test_visible_names_and_domains(self, catalog: MartCatalog) -> None:
        assert catalog.visible_names() == frozenset({"fct_orders", "dim_customers"})
        assert catalog.domains() == ("customers", "sales")

    def test_mart_columns_name_every_column_of_every_visible_mart(self, catalog: MartCatalog) -> None:
        assert catalog.mart_columns() == {
            "fct_orders": frozenset({"order_id", "customer_id", "order_date", "net_revenue", "current_status"}),
            "dim_customers": frozenset({"customer_id", "email", "customer_segment"}),
        }


class TestCatalogShape:
    @pytest.mark.parametrize("manifest", ["{}", "[]", '{"nodes": 3}'])
    def test_wrong_shape_raises_catalog_unavailable(self, target: Path, manifest: str) -> None:
        (target / "manifest.json").write_text(manifest, encoding="utf-8")

        with pytest.raises(CatalogUnavailable, match="make build-prod"):
            MartCatalogLoader(target).load()

    @pytest.mark.parametrize("primary_key", [None, [], ""])
    def test_mart_without_primary_key_is_unavailable(self, target: Path, primary_key: object) -> None:
        def drop_primary_key(manifest: dict[str, Any]) -> None:
            manifest["nodes"]["model.sherpa.dim_customers"]["meta"]["primary_key"] = primary_key

        _edit_manifest(target, drop_primary_key)

        with pytest.raises(CatalogUnavailable, match=r"mart dim_customers has no meta.primary_key"):
            MartCatalogLoader(target).load()

    def test_message_does_not_expose_the_path(self, target: Path) -> None:
        (target / "catalog.json").unlink()

        with pytest.raises(CatalogUnavailable) as error:
            MartCatalogLoader(target).load()

        assert str(target) not in str(error.value)
