from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from sherpa_mcp.catalog import MartCatalogLoader
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response  # noqa: TC002  (FastAPI reads the route's return annotation)

from sherpa_ui.api import catalog, health, queries
from sherpa_ui.api.errors import install_error_handlers
from sherpa_ui.api.queries import QueryService
from sherpa_ui.catalog.catalog_service import CatalogService
from sherpa_ui.health.health_service import HealthService
from sherpa_ui.readonly.query_runner import QueryRunner

if TYPE_CHECKING:
    from pathlib import Path

    from sherpa_ui.settings import UiSettings

# The UI is served on the loopback only (UI spec §3); a request for any other Host header is a DNS-rebinding attempt.
ALLOWED_HOSTS: Final = ("localhost", "127.0.0.1")
API_PREFIX: Final = "/api"
INDEX_FILE: Final = "index.html"


def create_app(settings: UiSettings, static_dir: Path | None) -> FastAPI:
    """The API under `/api`; with a `static_dir` that exists, also the built frontend, where every other GET answers
    `index.html` so the browser's router can take over."""
    app = FastAPI(title="SQL Sherpa UI")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(ALLOWED_HOSTS))
    install_error_handlers(app)

    loader = MartCatalogLoader(settings.ui_dbt_target_path)
    runner = QueryRunner(settings, loader)
    app.state.runner = runner
    app.state.query_service = QueryService(runner)
    app.state.catalog_service = CatalogService(loader, settings.db_name)
    app.state.health_service = HealthService(settings, loader)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(catalog.router)
    api.include_router(queries.router)
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

        candidate = (root / path).resolve()
        if candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)

        return FileResponse(root / INDEX_FILE)
