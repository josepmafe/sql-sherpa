from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sherpa_ui.app import create_app
from sherpa_ui.openapi_export import main as export_openapi

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.settings import UiSettings

INDEX_HTML = "<html><body>sherpa</body></html>"


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    return root


class TestApp:
    def test_untrusted_host_rejected(self, settings: UiSettings) -> None:
        with TestClient(create_app(settings, static_dir=None), base_url="http://localhost") as client:
            response = client.get("/api/catalog", headers={"Host": "evil.example"})

        assert response.status_code == 400

    @pytest.mark.parametrize("host", ["localhost", "localhost:8090", "127.0.0.1:8090"])
    def test_loopback_hosts_accepted(self, settings: UiSettings, host: str) -> None:
        with TestClient(create_app(settings, static_dir=None), base_url="http://localhost") as client:
            response = client.get("/api/catalog", headers={"Host": host})

        assert response.status_code == 503  # past the host check; the artifacts are missing in this fixture

    def test_spa_fallback_and_api_404(self, settings: UiSettings, static_dir: Path) -> None:
        with TestClient(create_app(settings, static_dir=static_dir), base_url="http://localhost") as client:
            deep_link = client.get("/explorer/tables/fct_orders")
            asset = client.get("/assets/app.js")
            unknown_api = client.get("/api/nope")
            api_root = client.get("/api")

        assert deep_link.status_code == 200
        assert deep_link.text == INDEX_HTML
        assert asset.text == "console.log(1)"
        assert unknown_api.status_code == 404
        assert unknown_api.json() == {"code": "not_found", "message": "Not Found"}
        assert api_root.status_code == 404
        assert api_root.json()["code"] == "not_found"

    def test_static_path_traversal_falls_back_to_index(self, settings: UiSettings, static_dir: Path) -> None:
        with TestClient(create_app(settings, static_dir=static_dir), base_url="http://localhost") as client:
            response = client.get("/%2e%2e/secret.txt")

        assert response.text == INDEX_HTML

    def test_missing_static_dir_serves_only_the_api(self, settings: UiSettings, tmp_path: Path) -> None:
        with TestClient(create_app(settings, static_dir=tmp_path / "absent"), base_url="http://localhost") as client:
            response = client.get("/")

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_unknown_api_route_without_static_is_json_404(self, settings: UiSettings) -> None:
        with TestClient(create_app(settings, static_dir=None), base_url="http://localhost") as client:
            response = client.get("/api/nope")

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_wrong_method_is_json_405(self, settings: UiSettings) -> None:
        with TestClient(create_app(settings, static_dir=None), base_url="http://localhost") as client:
            response = client.get("/api/queries/format")

        assert response.status_code == 405
        assert response.json()["code"] == "method_not_allowed"


class TestOpenapiExport:
    def test_writes_the_openapi_json(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "openapi.json"

        status = export_openapi([str(target)])

        schema = json.loads(target.read_text(encoding="utf-8"))
        assert status == 0
        assert {"/api/health", "/api/catalog", "/api/catalog/tables/{name}", "/api/queries"} <= set(schema["paths"])
        assert "/api/queries/{query_id}/cancel" in schema["paths"]

    def test_wrong_argument_count_is_a_usage_error(self) -> None:
        assert export_openapi([]) == 2
