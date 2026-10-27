from __future__ import annotations

from typing import Any

import pytest

GENERATED_AT = "2026-10-27T10:58:39.861036Z"


@pytest.fixture
def manifest() -> dict[str, Any]:
    """A small project:

    source shop.orders / shop.customers / crm.leads -> staging -> intermediate -> marts -> exposure, plus a seed
    and a snapshot. `dim_date` stands alone in the `shared` domain.
    """
    nodes = {
        "model.p.stg_shop__orders": _model("stg_shop__orders", "staging", ["source.p.shop.orders"]),
        "model.p.stg_shop__customers": _model("stg_shop__customers", "staging", ["source.p.shop.customers"]),
        "snapshot.p.snap_customers": _snapshot("snap_customers", ["model.p.stg_shop__customers"]),
        "seed.p.countries": _seed("countries"),
        "model.p.int_orders__enriched": _model(
            "int_orders__enriched",
            "intermediate",
            ["model.p.stg_shop__orders", "seed.p.countries", "macro.p.some_macro"],
        ),
        "model.p.fct_orders": _model("fct_orders", "marts", ["model.p.int_orders__enriched"], domain="sales"),
        "model.p.dim_customers": _model(
            "dim_customers", "marts", ["snapshot.p.snap_customers", "model.p.fct_orders"], domain="sales"
        ),
        "model.p.dim_date": _model("dim_date", "marts", [], domain="shared"),
        "test.p.not_null_fct_orders_order_id": _generic_test("not_null_fct_orders_order_id", "model.p.fct_orders"),
        "test.p.unique_dim_customers_id": _generic_test(
            "unique_dim_customers_id", "model.p.dim_customers", severity="WARN", column="id"
        ),
        "test.p.assert_orders_match_customers": {
            "resource_type": "test",
            "unique_id": "test.p.assert_orders_match_customers",
            "name": "assert_orders_match_customers",
            "config": {"severity": "warn"},
            "depends_on": {"macros": [], "nodes": ["model.p.fct_orders", "model.p.dim_customers", "macro.p.m"]},
        },
        "operation.p.p-on-run-end-0": {
            "resource_type": "operation",
            "unique_id": "operation.p.p-on-run-end-0",
            "name": "p-on-run-end-0",
            "original_file_path": "./dbt_project.yml",
            "package_name": "p",
            "depends_on": {"macros": [], "nodes": []},
        },
        "analysis.p.scratch": {
            "resource_type": "analysis",
            "unique_id": "analysis.p.scratch",
            "name": "scratch",
            "original_file_path": "analyses/scratch.sql",
            "package_name": "p",
            "depends_on": {"macros": [], "nodes": ["model.p.fct_orders"]},
        },
    }
    sources = {
        "source.p.shop.orders": _source("shop", "orders"),
        "source.p.shop.customers": _source("shop", "customers"),
        "source.p.crm.leads": _source("crm", "leads"),
    }
    exposures = {
        "exposure.p.sales_dashboard": {
            "resource_type": "exposure",
            "unique_id": "exposure.p.sales_dashboard",
            "name": "sales_dashboard",
            "package_name": "p",
            "original_file_path": "models/marts/sales/_exposures.yml",
            "description": "The weekly sales review.",
            "depends_on": {"macros": [], "nodes": ["model.p.fct_orders"]},
        }
    }
    unit_tests = {
        "unit_test.p.fct_orders.revenue_is_summed": {
            "resource_type": "unit_test",
            "unique_id": "unit_test.p.fct_orders.revenue_is_summed",
            "name": "revenue_is_summed",
            "model": "fct_orders",
            "depends_on": {"macros": [], "nodes": ["model.p.int_orders__enriched", "model.p.fct_orders"]},
        }
    }
    return {
        "metadata": {"generated_at": GENERATED_AT},
        "nodes": nodes,
        "sources": sources,
        "exposures": exposures,
        "unit_tests": unit_tests,
    }


def _model(name: str, folder: str, depends_on: list[str], domain: str | None = None) -> dict[str, Any]:
    meta = {"domain": domain} if domain else {}
    return {
        "resource_type": "model",
        "unique_id": f"model.p.{name}",
        "name": name,
        "package_name": "p",
        "fqn": ["p", folder, name],
        "original_file_path": f"models/{folder}/{name}.sql",
        "description": f"The {name} model.",
        "raw_code": f"select 1 as {name}",
        "config": {"materialized": "table", "meta": meta},
        "meta": meta,
        "contract": {"enforced": folder == "marts"},
        "columns": {"id": {"name": "id", "description": "The key.", "data_type": "bigint"}},
        "depends_on": {"macros": [], "nodes": depends_on},
    }


def _snapshot(name: str, depends_on: list[str]) -> dict[str, Any]:
    return {
        "resource_type": "snapshot",
        "unique_id": f"snapshot.p.{name}",
        "name": name,
        "package_name": "p",
        "fqn": ["p", name],
        "original_file_path": "snapshots/_snapshots.yml",
        "description": "",
        "raw_code": "",
        "config": {"materialized": "snapshot", "meta": {}},
        "columns": {},
        "depends_on": {"macros": [], "nodes": depends_on},
    }


def _seed(name: str) -> dict[str, Any]:
    return {
        "resource_type": "seed",
        "unique_id": f"seed.p.{name}",
        "name": name,
        "package_name": "p",
        "fqn": ["p", name],
        "original_file_path": f"seeds/{name}.csv",
        "description": "",
        "raw_code": "",
        "config": {"materialized": "seed", "meta": {}},
        "columns": {},
        "depends_on": {"macros": [], "nodes": []},
    }


def _source(source: str, table: str) -> dict[str, Any]:
    return {
        "resource_type": "source",
        "unique_id": f"source.p.{source}.{table}",
        "name": table,
        "source_name": source,
        "package_name": "p",
        "fqn": ["p", "staging", source, source, table],
        "original_file_path": f"models/staging/{source}/_{source}__sources.yml",
        "description": f"{source}.{table}",
        "config": {"meta": {}},
        "columns": {"id": {"name": "id", "description": "The key."}},
    }


def _generic_test(name: str, attached: str, severity: str = "ERROR", column: str = "order_id") -> dict[str, Any]:
    return {
        "resource_type": "test",
        "unique_id": f"test.p.{name}",
        "name": name,
        "attached_node": attached,
        "column_name": column,
        "test_metadata": {"name": name.split("_")[0], "kwargs": {}},
        "config": {"severity": severity},
        "depends_on": {"macros": ["macro.dbt.test_not_null"], "nodes": [attached]},
    }
