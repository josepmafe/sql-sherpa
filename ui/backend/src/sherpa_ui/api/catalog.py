from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from sherpa_ui.api.errors import ApiException
from sherpa_ui.catalog.catalog_service import CatalogService, CatalogTree, TableDetails

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("")
async def get_catalog(request: Request) -> CatalogTree:
    service: CatalogService = request.app.state.catalog_service
    return await run_in_threadpool(service.tree)


@router.get("/tables/{name}")
async def get_table(name: str, request: Request) -> TableDetails:
    service: CatalogService = request.app.state.catalog_service
    details = await run_in_threadpool(service.table, name)
    if details is None:
        raise ApiException(404, "table_not_found", f"no mart named {name!r}")

    return details
