"""Application entry point.

Run locally:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import time

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from . import queue_service as qs
from .clock import DEMO_START, Clock, SimulatedClock
from .config import settings
from .db import init_db, session_scope
from .models import Clinic
from .routes_demo import reset_demo
from .routes_demo import router as demo_router
from .routes_patient import router as patient_router
from .routes_staff import router as staff_router
from .routes_text import router as text_router
from .simulator import simulator

log = logging.getLogger("clinic")
TICK_SECONDS = 1.0


def _ensure_clinic() -> None:
    with session_scope() as s:
        if s.query(Clinic).first() is None:
            s.add(Clinic(slug="sunrise", name="Sunrise Community Clinic", name_es="Clínica Comunitaria Sunrise",
                         phone_display="(555) 010-2200", opens=time(8, 0), closes=time(17, 0), providers_working=2,
                         line_opens=time(7, 30), last_join=time(16, 30), open_weekdays="01234"))


def run_tick() -> None:
    at = qs.now()
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        if settings.demo_mode:
            simulator.tick(s, clinic, at)
        qs.tick(s, clinic, at)


async def _scheduler(stop: asyncio.Event) -> None:
    beats = 0
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_tick)
            beats += 1
            if beats % 600 == 0:
                with session_scope() as s:
                    qs.purge_personal_data(s, qs.now())
        except Exception:  # keep the loop alive; errors are logged
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.demo_mode:
        qs.runtime.clock = SimulatedClock(DEMO_START, settings.demo_speed)
        reset_demo(settings.demo_start_at)
    else:
        qs.runtime.clock = Clock()
        _ensure_clinic()
    stop = asyncio.Event()
    task = asyncio.create_task(_scheduler(stop)) if app.state.run_scheduler else None
    yield
    stop.set()
    if task:
        await task


def create_app(run_scheduler: bool = True) -> FastAPI:
    app = FastAPI(title="Clinic virtual queue", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.run_scheduler = run_scheduler

    @app.middleware("http")
    async def remember_base_url(request: Request, call_next):
        if not settings.public_base_url:
            qs.runtime.base_url = str(request.base_url).rstrip("/")
        else:
            qs.runtime.base_url = settings.public_base_url.rstrip("/")
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.get("/health", response_class=PlainTextResponse)
    def health() -> str:
        with session_scope() as s:
            qs.get_clinic(s)
        return "ok"

    app.include_router(patient_router)
    app.include_router(text_router)
    app.include_router(staff_router)
    app.include_router(demo_router)
    return app


app = create_app()
