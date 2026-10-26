from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import pytest
from fastapi.testclient import TestClient
from sherpa_mcp.catalog import Mart, MartCatalog, MartCatalogLoader, MartColumn, MetricDefinition
from sherpa_ui.app import create_app
from sherpa_ui.readonly.query_runner import QueryRunner

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sherpa_ui.settings import UiSettings

LOCAL_URL = "http://localhost"
REPO_ROOT = Path(__file__).resolve().parents[4]


def make_column(name: str, *, pk: bool = False, pii: bool = False, metric_refs: tuple[str, ...] = ()) -> MartColumn:
    return MartColumn(
        name=name,
        data_type="text",
        description=f"{name} column",
        is_pii=pii,
        is_primary_key=pk,
        accepted_values=("a", "b") if name == "status" else None,
        synonyms=(),
        metric_refs=metric_refs,
    )


def make_mart(name: str, domain: str) -> Mart:
    return Mart(
        name=name,
        description=f"{name} mart",
        domain=domain,
        grain="one row per thing",
        primary_key=(f"{name}_id",),
        columns=(make_column(f"{name}_id", pk=True), make_column("status", metric_refs=("revenue",))),
        synonyms=("thing",),
        tests=("not_null", "unique"),
    )


@pytest.fixture
def catalog() -> MartCatalog:
    return MartCatalog(
        marts=(
            make_mart("fct_orders", "sales"),
            make_mart("dim_dates", "shared"),
            make_mart("dim_customers", "customers"),
            make_mart("dim_products", "sales"),
            make_mart("dim_agents", "shared"),
        ),
        metrics=(MetricDefinition(name="revenue", definition="sum of net item amounts"),),
        generated_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


@pytest.fixture
def loader(catalog: MartCatalog) -> MartCatalogLoader:
    fake = create_autospec(MartCatalogLoader, instance=True)
    fake.load.return_value = catalog
    return fake


@pytest.fixture
def runner() -> QueryRunner:
    return create_autospec(QueryRunner, instance=True)


@pytest.fixture
def app(settings: UiSettings, loader: MartCatalogLoader, runner: QueryRunner) -> FastAPI:
    """The real app, with the services' collaborators swapped for fakes."""
    from sherpa_ui.api.queries import QueryService
    from sherpa_ui.catalog.catalog_service import CatalogService
    from sherpa_ui.health.health_service import HealthService

    application = create_app(settings, static_dir=None)
    application.state.query_service = QueryService(runner)
    application.state.catalog_service = CatalogService(loader, settings.db_name)
    application.state.health_service = HealthService(settings, loader)
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL_URL) as test_client:
        yield test_client


@pytest.fixture
def warehouse_settings(ui_settings: UiSettings) -> UiSettings:
    """The real settings, with the dbt target path made absolute (the settings' default is relative to the root)."""
    return ui_settings.model_copy(update={"ui_dbt_target_path": REPO_ROOT / ui_settings.ui_dbt_target_path})


@pytest.fixture
def warehouse_client(warehouse_settings: UiSettings) -> Iterator[TestClient]:
    with TestClient(create_app(warehouse_settings, static_dir=None), base_url=LOCAL_URL) as test_client:
        yield test_client
