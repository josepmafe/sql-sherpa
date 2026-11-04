from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from sherpa_ui.home.home_service import Home, HomeService

router = APIRouter(prefix="/home", tags=["home"])


@router.get("")
async def get_home(request: Request) -> Home:
    service: HomeService = request.app.state.home_service
    return await run_in_threadpool(service.home)
