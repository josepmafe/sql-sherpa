from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from sherpa_ui.api.errors import INVALID_REQUEST_CODE, ApiException, error_responses
from sherpa_ui.lineage.lineage_service import ColumnFocusView, LineageGraph, LineageService, NodeDetails
from sherpa_ui.lineage.manifest_graph import Layer
from sherpa_ui.lineage.selector import UnsupportedSelector

router = APIRouter(prefix="/lineage", tags=["lineage"], responses=error_responses(503))


@router.get("", responses=error_responses(422))
async def get_lineage(
    request: Request,
    select: str = "",
    layers: str | None = None,
    failing_only: bool = False,
    columns: bool = False,
) -> LineageGraph:
    service: LineageService = request.app.state.lineage_service
    shown = _parse_layers(layers)
    try:
        return await run_in_threadpool(service.graph, select, shown, failing_only=failing_only, columns=columns)
    except UnsupportedSelector as exc:
        raise ApiException(422, "unsupported_selector", exc.message) from exc


@router.get("/nodes/{unique_id}", responses=error_responses(404, 422))
async def get_node(unique_id: str, request: Request) -> NodeDetails:
    service: LineageService = request.app.state.lineage_service
    details = await run_in_threadpool(service.node, unique_id)
    if details is None:
        raise ApiException(404, "node_not_found", f"no graph node with id {unique_id!r}")

    return details


@router.get("/columns/{unique_id}/{column}", responses=error_responses(404, 422))
async def get_column_focus(unique_id: str, column: str, request: Request) -> ColumnFocusView:
    service: LineageService = request.app.state.lineage_service
    focus = await run_in_threadpool(service.column_focus, unique_id, column)
    if focus is None:
        raise ApiException(404, "column_not_found", f"no column {column!r} on graph node {unique_id!r}")

    return focus


def _parse_layers(layers: str | None) -> frozenset[Layer] | None:
    if layers is None:
        return None

    try:
        return frozenset(Layer(name.strip()) for name in layers.split(",") if name.strip())
    except ValueError as exc:
        known = ", ".join(layer.value for layer in Layer)
        raise ApiException(422, INVALID_REQUEST_CODE, f"layers: {exc}; known layers: {known}") from exc
