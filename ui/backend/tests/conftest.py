from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_ui.readonly.query_runner import QueryRunner
from sherpa_ui.settings import UiSettings
from ui_helpers import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

LOCALHOST = "127.0.0.1"
GENERATED_AT = "2026-10-27T10:58:39.861036Z"
BACKEND_EXIT_SECONDS = 2.0  # how long the server process of a closed connection may take to leave pg_stat_activity
# By user name only: the admin is not a member of the UI role, so Postgres shows it every other column of the UI's
# sessions (backend_type, state, query) as NULL.
UI_SESSIONS = "select count(*) from pg_stat_activity where usename = %s"

_DESELECTED_FILES: set[Path] = set()


def pytest_deselected(items: list[pytest.Item]) -> None:
    """Note the files whose tests a marker expression left out, so `test_layout.py` can tell them from unseen ones."""
    _DESELECTED_FILES.update(Path(str(item.path)).resolve() for item in items)


@pytest.fixture(scope="session")
def deselected_files() -> frozenset[Path]:
    return frozenset(_DESELECTED_FILES)


class AdminSettings(BaseSettings):
    """The admin login the warehouse tests use for their checks, read from `.env`."""

    model_config = SettingsConfigDict(frozen=True, env_prefix="SHERPA_", env_file=".env", extra="ignore")

    db_host: str
    db_port: int
    db_name: str
    admin_user: str
    admin_password: SecretStr

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.admin_user,
            password=self.admin_password.get_secret_value(),
        )


@pytest.fixture(scope="session")
def ui_settings() -> UiSettings:
    return UiSettings()  # type: ignore[call-arg]  # fields come from the environment / .env


@pytest.fixture(scope="module")
def prod_loader(ui_settings: UiSettings) -> MartCatalogLoader:
    return MartCatalogLoader(REPO_ROOT / ui_settings.ui_dbt_target_path)


@pytest.fixture(scope="module")
def runner(ui_settings: UiSettings, prod_loader: MartCatalogLoader) -> QueryRunner:
    return QueryRunner(ui_settings, prod_loader)


@pytest.fixture(scope="session")
def admin_settings() -> AdminSettings:
    return AdminSettings()  # type: ignore[call-arg]


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    with psycopg.connect(AdminSettings().conninfo(), autocommit=True) as conn:  # type: ignore[call-arg]
        yield conn


@pytest.fixture
def ui_sessions(admin: psycopg.Connection, ui_settings: UiSettings) -> Callable[[], int]:
    """How many sessions the UI role has open right now."""

    def count() -> int:
        row = admin.execute(UI_SESSIONS, [ui_settings.ui_user]).fetchone()
        assert row is not None
        return int(row[0])

    return count


@pytest.fixture
def leaked_ui_backends(ui_sessions: Callable[[], int]) -> Callable[[], int]:
    """How many more server processes the UI role has open than when the test started, once closed connections
    have had `BACKEND_EXIT_SECONDS` to go away."""
    baseline = ui_sessions()

    def leaked() -> int:
        deadline = time.monotonic() + BACKEND_EXIT_SECONDS
        current = ui_sessions()
        while current > baseline and time.monotonic() < deadline:
            time.sleep(0.05)
            current = ui_sessions()
        return max(current - baseline, 0)

    return leaked


@pytest.fixture
def settings(tmp_path: Path) -> UiSettings:
    return UiSettings(
        _env_file=None,  # type: ignore[call-arg]
        db_host="127.0.0.1",
        db_port=1,
        db_name="sherpa",
        ui_user="sherpa_ui",
        ui_password=SecretStr("x"),
        ui_dbt_target_path=tmp_path / "missing-target",
    )


@pytest.fixture
def offline_settings(settings: UiSettings, monkeypatch: pytest.MonkeyPatch) -> UiSettings:
    """The settings of a warehouse that refuses every connection. The refusal is raised directly: connecting to a
    closed port takes the whole connect timeout on Windows."""

    def refused(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError("connection failed: connection refused")

    monkeypatch.setattr(psycopg, "connect", refused)
    return settings


@pytest.fixture
def ui_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment of a UI process: what `UiSettings()` and the apps built from the environment read."""
    monkeypatch.setenv("SHERPA_DB_HOST", "db.example")
    monkeypatch.setenv("SHERPA_DB_PORT", "5433")
    monkeypatch.setenv("SHERPA_DB_NAME", "sherpa")
    monkeypatch.setenv("SHERPA_UI_USER", "sherpa_ui")
    monkeypatch.setenv("SHERPA_UI_PASSWORD", "s3cret pw")
    monkeypatch.delenv("SHERPA_UI_STATEMENT_TIMEOUT_S", raising=False)
    monkeypatch.delenv("SHERPA_UI_DBT_TARGET_PATH", raising=False)


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
            "fqn": ["p", "singular", "assert_orders_match_customers"],
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
            "fqn": ["p", "scratch"],
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
        "database": "warehouse",
        "schema": "analytics",
        "alias": name,
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
        "database": "warehouse",
        "schema": f"raw_{source}",
        "identifier": table,
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
