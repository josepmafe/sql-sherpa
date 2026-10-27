from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool

from sherpa_ui.api.errors import INVALID_REQUEST_CODE, ApiException, error_responses
from sherpa_ui.lineage.lineage_service import LineageGraph, LineageService, NodeDetails
from sherpa_ui.lineage.manifest_graph import Layer
from sherpa_ui.lineage.selector import MAX_SELECTOR_LENGTH, UnsupportedSelector

router = APIRouter(prefix="/lineage", tags=["lineage"], responses=error_responses(503))


@router.get("", responses=error_responses(422))
async def get_lineage(
    request: Request,
    select: Annotated[str, Query(max_length=MAX_SELECTOR_LENGTH)] = "",
    layers: str | None = None,
    failing_only: bool = False,
) -> LineageGraph:
    service: LineageService = request.app.state.lineage_service
    shown = _parse_layers(layers)
    try:
        return await run_in_threadpool(service.graph, select, shown, failing_only=failing_only)
    except UnsupportedSelector as exc:
        raise ApiException(422, "unsupported_selector", exc.message) from exc


@router.get("/nodes/{unique_id}", responses=error_responses(404, 422))
async def get_node(unique_id: str, request: Request) -> NodeDetails:
    service: LineageService = request.app.state.lineage_service
    details = await run_in_threadpool(service.node, unique_id)
    if details is None:
        raise ApiException(404, "node_not_found", f"no graph node with id {unique_id!r}")

    return details


def _parse_layers(layers: str | None) -> frozenset[Layer] | None:
    if layers is None:
        return None

    try:
        return frozenset(Layer(name.strip()) for name in layers.split(",") if name.strip())
    except ValueError as exc:
        known = ", ".join(layer.value for layer in Layer)
        raise ApiException(422, INVALID_REQUEST_CODE, f"layers: {exc}; known layers: {known}") from exc
