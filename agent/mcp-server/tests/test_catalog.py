from __future__ import annotations

import json
import logging
import os
import shutil
from typing import TYPE_CHECKING, Any

import pytest
from sherpa_mcp.catalog import CatalogUnavailable, MartCatalog, MartCatalogLoader, UnmaskedArtifacts

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def catalog(fixture_target: Path) -> MartCatalog:
    return MartCatalogLoader(fixture_target).load()


def _edit_manifest(fixture_target: Path, edit: Any) -> None:
    path = fixture_target / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    edit(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _bump_mtime(path: Path, seconds: int) -> None:
    """A modification time `seconds` later, so the loader sees a change even on a coarse file-system clock."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + seconds * 1_000_000_000))


def _make_orders_public(manifest: dict[str, Any]) -> None:
    manifest["nodes"]["model.sherpa.fct_orders"]["access"] = "public"


# Data platform spec §6.5: the sentence that ends the description of every masked column.
PSEUDONYMISED_SENTENCE = (
    "Pseudonymised: an md5 hash of the value and a secret salt. Equal values hash equally, so counts, joins and "
    "group by work, but comparing it with a plain value, LIKE or a string function matches nothing; identify "
    "customers by customer_id."
)


@pytest.mark.warehouse
def test_masked_columns_are_the_specs(prod_loader: MartCatalogLoader) -> None:
    marts = prod_loader.load().marts

    masked = [(mart.name, column) for mart in marts for column in mart.columns if column.is_pii]

    assert {(name, column.name) for name, column in masked} == {
        ("dim_customers", "email"),
        ("dim_customers", "first_name"),
        ("dim_customers", "last_name"),
    }
    assert all(column.description.endswith(PSEUDONYMISED_SENTENCE) for _, column in masked)


class TestMartCatalogLoader:
    def test_loads_only_marts_schema_models(self, catalog: MartCatalog) -> None:
        assert sorted(mart.name for mart in catalog.marts) == ["dim_customers", "dim_products", "fct_orders"]

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

    def test_public_access_rule_applies_once_any_model_is_public(self, fixture_target: Path) -> None:
        _edit_manifest(fixture_target, _make_orders_public)

        loaded = MartCatalogLoader(fixture_target).load()

        assert loaded.visible_marts() == frozenset({"fct_orders"})

    def test_reloads_on_mtime_change(self, fixture_target: Path) -> None:
        loader = MartCatalogLoader(fixture_target)
        first = loader.load()

        assert loader.load() is first

        _edit_manifest(fixture_target, _make_orders_public)
        _bump_mtime(fixture_target / "manifest.json", seconds=5)
        reloaded = loader.load()

        assert reloaded is not first
        assert reloaded.visible_marts() == frozenset({"fct_orders"})

    def test_reloads_when_only_catalog_json_changes(self, fixture_target: Path) -> None:
        loader = MartCatalogLoader(fixture_target)
        first = loader.load()

        _bump_mtime(fixture_target / "catalog.json", seconds=5)

        assert loader.load() is not first

    def test_missing_artifacts_raise_catalog_unavailable(
        self, fixture_target: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        missing_folder = tmp_path_factory.mktemp("empty") / "nope"
        (fixture_target / "catalog.json").unlink()

        with pytest.raises(CatalogUnavailable, match="make build-prod") as no_folder:
            MartCatalogLoader(missing_folder).load()
        with pytest.raises(CatalogUnavailable, match="make build-prod") as no_catalog:
            MartCatalogLoader(fixture_target).load()

        assert "make docs-prod" not in str(no_folder.value)
        assert "catalog.json" in str(no_catalog.value)

    def test_unparsable_artifact_raises_catalog_unavailable(self, fixture_target: Path) -> None:
        (fixture_target / "manifest.json").write_text("{not json", encoding="utf-8")

        with pytest.raises(CatalogUnavailable, match="make build-prod"):
            MartCatalogLoader(fixture_target).load()

    @pytest.mark.parametrize(
        ("broken", "reason"),
        [
            ('{"metadata": {"generated_at": "2026-', "cannot read manifest.json"),  # half-written by a build
            ("{}", "unexpected layout in manifest.json or catalog.json"),
            (None, "dbt artifacts not found (manifest.json, catalog.json)"),  # removed
        ],
        ids=["half_written", "wrong_layout", "removed"],
    )
    def test_a_failed_reload_keeps_the_last_catalog(
        self, fixture_target: Path, caplog: pytest.LogCaptureFixture, broken: str | None, reason: str
    ) -> None:
        manifest = fixture_target / "manifest.json"
        loader = MartCatalogLoader(fixture_target)
        first = loader.load()
        good_manifest = manifest.read_text(encoding="utf-8")
        if broken is None:
            manifest.unlink()
        else:
            manifest.write_text(broken, encoding="utf-8")
            _bump_mtime(manifest, seconds=5)

        with caplog.at_level(logging.WARNING, logger="sherpa_mcp.catalog"):
            kept = loader.load()

        manifest.write_text(good_manifest, encoding="utf-8")
        _bump_mtime(manifest, seconds=10)
        repaired = loader.load()

        assert kept is first
        assert [record.getMessage() for record in caplog.records] == [
            "could not reload the dbt artifacts, so the catalog generated at 2026-10-24T12:36:42.863017+00:00 "
            f"stays in use: {reason}: run `make build-prod` (it writes both files)"
        ]
        assert repaired is not first
        assert repaired == first

    def test_a_still_broken_reload_is_retried_and_warned_on_every_load(
        self, fixture_target: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        manifest = fixture_target / "manifest.json"
        loader = MartCatalogLoader(fixture_target)
        loader.load()
        manifest.write_text("{not json", encoding="utf-8")
        _bump_mtime(manifest, seconds=5)

        with caplog.at_level(logging.WARNING, logger="sherpa_mcp.catalog"):
            loader.load()
            loader.load()

        assert len(caplog.records) == 2

    def test_accepted_values_come_only_from_tests_attached_to_the_mart(self, fixture_target: Path) -> None:
        def share_status_accepted_values_with_customers(manifest: dict[str, Any]) -> None:
            manifest["nodes"]["model.sherpa.dim_customers"]["columns"]["current_status"] = {
                "name": "current_status",
                "description": "",
                "data_type": "text",
                "meta": {},
                "doc_blocks": [],
            }
            accepted = manifest["nodes"][
                "test.sherpa.accepted_values_fct_orders_current_status__placed__shipped__cancelled"
            ]
            accepted["depends_on"]["nodes"].append("model.sherpa.dim_customers")

        _edit_manifest(fixture_target, share_status_accepted_values_with_customers)

        catalog = MartCatalogLoader(fixture_target).load()

        orders = catalog.mart("fct_orders")
        customers = catalog.mart("dim_customers")
        assert orders is not None
        assert customers is not None
        orders_status = orders.column("current_status")
        customers_status = customers.column("current_status")
        assert orders_status is not None
        assert customers_status is not None
        assert orders_status.accepted_values == ("placed", "shipped", "cancelled")
        assert customers_status.accepted_values is None


class TestMartCatalog:
    def test_lookup_is_case_insensitive(self, catalog: MartCatalog) -> None:
        orders = catalog.mart("FCT_Orders")

        assert orders is not None
        assert orders.name == "fct_orders"
        assert orders.column("NET_Revenue") is not None
        assert catalog.mart("stg_shop__customers") is None
        assert orders.column("nope") is None

    def test_is_pii_is_the_column_s_meta_masked(self, catalog: MartCatalog) -> None:
        customers = catalog.mart("dim_customers")
        products = catalog.mart("dim_products")

        assert customers is not None
        assert products is not None
        assert [column.name for column in customers.columns if column.is_pii] == ["email"]
        assert [column.name for column in products.columns if column.is_pii] == ["supplier_email"]

    def test_an_unmasked_table_is_not_a_visible_mart(self, catalog: MartCatalog) -> None:
        assert catalog.visible_marts() == frozenset({"fct_orders", "dim_customers", "dim_products"})
        assert catalog.mart("dim_customers_unmasked") is None

    def test_visible_marts_and_domains(self, catalog: MartCatalog) -> None:
        assert catalog.visible_marts() == frozenset({"fct_orders", "dim_customers", "dim_products"})
        assert catalog.domains() == ("customers", "products", "sales")


class TestCatalogShape:
    @pytest.mark.parametrize("manifest", ["{}", "[]", '{"nodes": 3}'])
    def test_wrong_shape_raises_catalog_unavailable(self, fixture_target: Path, manifest: str) -> None:
        (fixture_target / "manifest.json").write_text(manifest, encoding="utf-8")

        with pytest.raises(CatalogUnavailable, match="make build-prod"):
            MartCatalogLoader(fixture_target).load()

    @pytest.mark.parametrize("primary_key", [None, [], ""])
    def test_mart_without_primary_key_is_unavailable(self, fixture_target: Path, primary_key: object) -> None:
        def drop_primary_key(manifest: dict[str, Any]) -> None:
            manifest["nodes"]["model.sherpa.dim_customers"]["meta"]["primary_key"] = primary_key

        _edit_manifest(fixture_target, drop_primary_key)

        with pytest.raises(CatalogUnavailable, match=r"mart dim_customers has no meta.primary_key"):
            MartCatalogLoader(fixture_target).load()

    @pytest.mark.parametrize(
        ("folder", "command"), [("target-prod", "make build-prod"), ("target-eval", "make eval-db")]
    )
    def test_a_build_with_pii_in_a_visible_mart_replaces_the_cached_catalog(
        self, fixture_target: Path, tmp_path_factory: pytest.TempPathFactory, folder: str, command: str
    ) -> None:
        target = tmp_path_factory.mktemp("targets") / folder
        shutil.copytree(fixture_target, target)
        loader = MartCatalogLoader(target)
        loader.load()

        def raw_order_id(manifest: dict[str, Any]) -> None:
            manifest["nodes"]["model.sherpa.fct_orders"]["columns"]["order_id"]["meta"]["pii"] = True

        _edit_manifest(target, raw_order_id)
        _bump_mtime(target / "manifest.json", seconds=5)

        for _ in range(2):  # the second call proves the cached catalog is gone, not kept
            with pytest.raises(UnmaskedArtifacts, match=rf"column with meta\.pii \(order_id\).*run {command}$"):
                loader.load()

    def test_message_does_not_expose_the_path(self, fixture_target: Path) -> None:
        (fixture_target / "catalog.json").unlink()

        with pytest.raises(CatalogUnavailable) as error:
            MartCatalogLoader(fixture_target).load()

        assert str(fixture_target) not in str(error.value)
