from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from sherpa_mcp.catalog import MartCatalogLoader
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response  # noqa: TC002  (FastAPI reads the route's return annotation)

from sherpa_ui.api import catalog, health, lineage, queries
from sherpa_ui.api.body_limit import BodySizeLimit
from sherpa_ui.api.errors import error_responses, install_error_handlers
from sherpa_ui.api.queries import QueryService
from sherpa_ui.build.build_status import BuildStatusReader
from sherpa_ui.catalog.catalog_service import CatalogService
from sherpa_ui.health.health_service import HealthService
from sherpa_ui.lineage.lineage_service import LineageService
from sherpa_ui.lineage.manifest_loader import ManifestLoader
from sherpa_ui.lineage.node_sql import NodeSql
from sherpa_ui.readonly.query_runner import QueryRunner

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.settings import UiSettings

# The UI is served on the loopback only (UI spec §3); a request for any other Host header is a DNS-rebinding attempt.
ALLOWED_HOSTS: Final = ("localhost", "127.0.0.1")
API_PREFIX: Final = "/api"
INDEX_FILE: Final = "index.html"
# The page names the current build's hashed assets, so the browser revalidates it on every load.
PAGE_HEADERS: Final = {"Cache-Control": "no-cache"}
ASSETS_DIR: Final = "assets"  # Vite's folder for the hashed scripts and styles: a missing one is a 404, not the page
CONTAINER_STATIC_DIR: Final = "/app/static"


def create_app(settings: UiSettings, static_dir: Path | None) -> FastAPI:
    """The API under `/api`; with a `static_dir` that exists, also the built frontend, where every other GET answers
    `index.html` so the browser's router can take over."""
    # No Swagger UI or ReDoc pages: they load their scripts from a CDN, and the UI works offline (UI spec §1,
    # criterion 1). The schema itself stays at /openapi.json.
    app = FastAPI(title="SQL Sherpa UI", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(ALLOWED_HOSTS))
    app.add_middleware(BodySizeLimit)
    install_error_handlers(app)

    loader = MartCatalogLoader(settings.ui_dbt_target_path)
    runner = QueryRunner(settings, loader)
    app.state.runner = runner
    app.state.query_service = QueryService(runner)
    app.state.catalog_service = CatalogService(loader, settings.db_name)
    app.state.health_service = HealthService(settings, loader)
    app.state.lineage_service = LineageService(
        ManifestLoader(settings.ui_dbt_target_path), BuildStatusReader(settings), NodeSql(settings.ui_dbt_target_path)
    )

    api = APIRouter(prefix=API_PREFIX, responses=error_responses(500))  # the catch-all handler's internal_error
    api.include_router(health.router)
    api.include_router(catalog.router)
    api.include_router(queries.router)
    api.include_router(lineage.router)
    app.include_router(api)

    if static_dir is not None and static_dir.is_dir():
        _serve_frontend(app, static_dir.resolve())

    return app


def _serve_frontend(app: FastAPI, root: Path) -> None:
    # Registered after the API routes, so they win; only a path under /api that matched no route reaches this.
    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> Response:
        if f"/{path}".startswith(f"{API_PREFIX}/") or f"/{path}" == API_PREFIX:
            raise HTTPException(404, "Not Found")

        index = root / INDEX_FILE
        candidate = (root / path).resolve()
        if candidate.is_file() and root in candidate.parents and candidate != index:
            return FileResponse(candidate)

        if path.startswith(f"{ASSETS_DIR}/"):  # a page from an older build asking for its own hashed files
            raise HTTPException(404, "Not Found")

        return FileResponse(index, headers=PAGE_HEADERS)


def __getattr__(name: str) -> FastAPI:
    # `uvicorn sherpa_ui.app:dev_app --reload` (make ui-dev): the API alone, settings read from the environment.
    # Built on first access, so importing this module (tests, the OpenAPI export) needs no database settings.
    if name == "dev_app":
        from sherpa_ui.settings import UiSettings

        return create_app(UiSettings(), static_dir=None)

    # `uvicorn sherpa_ui.app:container_app` (ui/Dockerfile): the API plus the frontend the image built into /app/static.
    if name == "container_app":
        from pathlib import Path

        from sherpa_ui.settings import UiSettings

        return create_app(UiSettings(), static_dir=Path(CONTAINER_STATIC_DIR))

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
