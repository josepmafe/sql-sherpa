from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

from sherpa_ui.home.data_status import DataStatus
from sherpa_ui.home.figures import Tile, TrendWeek
from sherpa_ui.home.home_service import Home, HomeFigures, HomeService

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient


def make_home() -> Home:
    tile = Tile(
        id="net_revenue",
        title="Net revenue",
        format="currency",
        decimals=0,
        up_is_good=True,
        note=None,
        value=Decimal("34811.50"),
        change=Decimal("0.12"),
        change_kind="relative",
        direction="up",
        good=True,
    )
    week = TrendWeek(
        week_start=date(2026, 10, 26), net_revenue=Decimal("8000.50"), is_black_friday_week=False, is_partial=True
    )
    status = DataStatus(as_of_date=date(2026, 10, 31), build=None, build_note="n", freshness=None, freshness_note="f")
    return Home(figures=HomeFigures(tiles=(tile,), trend=(week,)), figures_error=None, status=status)


class TestHomeApi:
    def test_returns_the_model_with_money_as_strings(self, app: FastAPI, client: TestClient) -> None:
        service = create_autospec(HomeService, instance=True)
        service.home.return_value = make_home()
        app.state.home_service = service

        response = client.get("/api/home")

        assert response.status_code == 200
        body = response.json()
        assert body["figures"]["tiles"][0]["value"] == "34811.50"
        assert body["figures"]["trend"][0]["net_revenue"] == "8000.50"
        assert body["figures"]["trend"][0]["week_start"] == "2026-10-26"
        assert body["figures_error"] is None
        assert body["status"]["as_of_date"] == "2026-10-31"

    def test_schema_declares_home_with_only_the_internal_error(self, client: TestClient) -> None:
        operation = client.get("/openapi.json").json()["paths"]["/api/home"]["get"]

        assert operation["tags"] == ["home"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/Home")
        assert set(operation["responses"]) == {"200", "500"}
