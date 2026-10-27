from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sherpa_ui.api.errors import (
    INTERNAL_ERROR_CODE,
    INVALID_REQUEST_CODE,
    PII_COLUMN_CODE,
    QUERY_ERROR_STATUS,
    UNKNOWN_COLUMN_CODE,
)
from sherpa_ui.app import create_app
from sherpa_ui.openapi_export import main as export_openapi

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.settings import UiSettings

INDEX_HTML = "<html><body>sherpa</body></html>"
API_ERROR = "#/components/schemas/ApiError"


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

    @pytest.mark.parametrize("path", ["/", "/explorer/tables/fct_orders", "/index.html"])
    def test_the_page_is_revalidated_on_every_load(self, settings: UiSettings, static_dir: Path, path: str) -> None:
        # index.html names the build's hashed assets, so a cached copy would load the previous build's.
        with TestClient(create_app(settings, static_dir=static_dir), base_url="http://localhost") as client:
            response = client.get(path)

        assert response.text == INDEX_HTML
        assert response.headers["cache-control"] == "no-cache"

    def test_a_missing_asset_is_404_not_the_page(self, settings: UiSettings, static_dir: Path) -> None:
        with TestClient(create_app(settings, static_dir=static_dir), base_url="http://localhost") as client:
            missing = client.get("/assets/index-0ld.js")
            present = client.get("/assets/app.js")

        assert missing.status_code == 404
        assert missing.json() == {"code": "not_found", "message": "Not Found"}
        assert (present.status_code, present.text) == (200, "console.log(1)")
        assert "no-cache" not in present.headers.get("cache-control", "")

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

    def test_serves_the_openapi_schema_but_no_docs_pages(self, settings: UiSettings) -> None:
        # Swagger UI and ReDoc load their scripts from a CDN, and the UI works offline (UI spec §1, criterion 1).
        with TestClient(create_app(settings, static_dir=None), base_url="http://localhost") as client:
            docs = client.get("/docs")
            redoc = client.get("/redoc")
            schema = client.get("/openapi.json")

        assert (docs.status_code, redoc.status_code) == (404, 404)
        assert schema.status_code == 200
        assert "/api/queries" in schema.json()["paths"]

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


class TestOpenapiErrors:
    """The schema the frontend's client is generated from describes the errors the API really answers."""

    def test_every_error_response_is_an_api_error(self, settings: UiSettings) -> None:
        schema = create_app(settings, static_dir=None).openapi()

        errors = [
            response
            for operations in schema["paths"].values()
            for operation in operations.values()
            for status, response in operation["responses"].items()
            if int(status) >= 400
        ]

        assert errors
        assert all(error["content"]["application/json"]["schema"] == {"$ref": API_ERROR} for error in errors)
        assert {"HTTPValidationError", "ValidationError"}.isdisjoint(schema["components"]["schemas"])
        assert schema["components"]["schemas"]["ApiError"]["required"] == ["code", "message"]

    @pytest.mark.parametrize(
        ("path", "method", "statuses"),
        [
            ("/api/health", "get", {"200", "500"}),
            ("/api/catalog", "get", {"200", "500", "503"}),
            ("/api/catalog/tables/{name}", "get", {"200", "404", "422", "500", "503"}),
            ("/api/queries", "post", {"200", "409", "422", "429", "500", "503", "504"}),
            ("/api/queries/format", "post", {"200", "422", "500"}),
            ("/api/queries/{query_id}/cancel", "post", {"204", "422", "500"}),
            ("/api/lineage", "get", {"200", "422", "500", "503"}),
            ("/api/lineage/nodes/{unique_id}", "get", {"200", "404", "422", "500", "503"}),
        ],
    )
    def test_each_endpoint_declares_the_statuses_it_answers(
        self, settings: UiSettings, path: str, method: str, statuses: set[str]
    ) -> None:
        schema = create_app(settings, static_dir=None).openapi()

        assert set(schema["paths"][path][method]["responses"]) == statuses

    def test_each_error_status_names_its_codes(self, settings: UiSettings) -> None:
        responses = create_app(settings, static_dir=None).openapi()["paths"]["/api/queries"]["post"]["responses"]

        codes = {
            **QUERY_ERROR_STATUS,
            UNKNOWN_COLUMN_CODE: 422,
            PII_COLUMN_CODE: 422,
            INVALID_REQUEST_CODE: 422,
            INTERNAL_ERROR_CODE: 500,
        }
        assert all(f"`{code}`" in responses[str(status)]["description"] for code, status in codes.items())


class TestDevApp:
    def test_is_built_from_the_environment_without_static_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in {
            "SHERPA_DB_HOST": "unused",
            "SHERPA_DB_PORT": "1",
            "SHERPA_DB_NAME": "unused",
            "SHERPA_UI_USER": "unused",
            "SHERPA_UI_PASSWORD": "unused",
        }.items():
            monkeypatch.setenv(key, value)

        import sherpa_ui.app as app_module

        dev_app = app_module.dev_app  # type: ignore[attr-defined]

        with TestClient(dev_app, base_url="http://localhost") as client:
            not_served = client.get("/explorer")

        assert "/api/health" in dev_app.openapi()["paths"]
        assert not_served.status_code == 404

    def test_unknown_attribute_still_raises(self) -> None:
        import sherpa_ui.app as app_module

        with pytest.raises(AttributeError):
            app_module.nonexistent  # noqa: B018


class TestContainerApp:
    def test_serves_the_frontend_from_the_image_static_dir(
        self, monkeypatch: pytest.MonkeyPatch, static_dir: Path
    ) -> None:
        for key, value in {
            "SHERPA_DB_HOST": "unused",
            "SHERPA_DB_PORT": "1",
            "SHERPA_DB_NAME": "unused",
            "SHERPA_UI_USER": "unused",
            "SHERPA_UI_PASSWORD": "unused",
        }.items():
            monkeypatch.setenv(key, value)

        import sherpa_ui.app as app_module

        monkeypatch.setattr(app_module, "CONTAINER_STATIC_DIR", str(static_dir))
        container_app = app_module.container_app  # type: ignore[attr-defined]

        with TestClient(container_app, base_url="http://localhost") as client:
            page = client.get("/explorer")
            missing_api = client.get("/api/nope")

        assert page.text == INDEX_HTML
        assert missing_api.status_code == 404
