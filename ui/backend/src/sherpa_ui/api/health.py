from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from sherpa_ui.health.health_service import Health, HealthService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def get_health(request: Request) -> Health:
    service: HealthService = request.app.state.health_service
    return await run_in_threadpool(service.check)
