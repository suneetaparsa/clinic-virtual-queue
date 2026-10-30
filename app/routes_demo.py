"""Demo control page: simulated clock, reset, and links for each device."""
from __future__ import annotations

import socket
from datetime import datetime, time

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import queue_service as qs
from .clock import DEMO_START
from .config import settings
from .db import session_scope
from .messaging import demo_gateway
from .security import format_phone, rate_limiter
from .simulator import simulator
from .web import templates

router = APIRouter(prefix="/demo")


def _demo_only():
    if not settings.demo_mode:
        raise HTTPException(status_code=404)


def lan_addresses() -> list[str]:
    """Best guess at this computer's Wi-Fi address(es), for opening the demo on a phone."""
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))  # no packet is sent
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith("127.")]


@router.get("", response_class=HTMLResponse)
def control(request: Request):
    _demo_only()
    at = qs.now()
    port = request.url.port or 8000
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        line = qs.lineup(s, clinic, at.date())
        sample = [(e.line_id, e.first_name, format_phone(qs.security.decrypt_phone(e.phone_cipher))) for e in line if e.synthetic and e.phone_cipher][:3]
    ctx = {
        "request": request,
        "clock": qs.runtime.clock,
        "at": at,
        "clinic": clinic,
        "addresses": [f"http://{ip}:{port}" for ip in lan_addresses()],
        "here": str(request.base_url).rstrip("/"),
        "sample": sample,
        "one_time_code": settings.join_one_time_code,
        "starts": STARTS,
        "phase": qs.phase(clinic, at),
    }
    return templates.TemplateResponse(request, "demo.html", ctx)


@router.post("/speed")
def speed(value: float = Form(10)):
    _demo_only()
    qs.runtime.clock.set_speed(max(1.0, min(120.0, value)))
    return RedirectResponse("/demo", status_code=303)


@router.post("/run")
def run(running: int = Form(1)):
    _demo_only()
    qs.runtime.clock.set_running(bool(running))
    return RedirectResponse("/demo", status_code=303)


@router.post("/skip")
def skip(minutes: float = Form(10)):
    _demo_only()
    qs.runtime.clock.advance(max(1.0, min(120.0, minutes)))
    return RedirectResponse("/demo", status_code=303)


STARTS = {
    "morning": ("8:15 AM, busy line", time(8, 15)),
    "early": ("7:00 AM, before opening", time(7, 0)),
    "late": ("4:20 PM, last check-ins", time(16, 20)),
    "after": ("5:05 PM, after closing", time(17, 5)),
}


@router.post("/reset")
def reset(confirm: str = Form(""), start: str = Form("morning")):
    _demo_only()
    if confirm != "yes":
        return RedirectResponse(f"/demo?confirm_reset=1&start={start}", status_code=303)
    reset_demo(start)
    return RedirectResponse("/demo", status_code=303)


def reset_demo(start: str = "morning") -> None:
    """Restart the demo day at one of the preset times. Later starts replay the
    simulated day from 8:15 AM so the dashboard has a realistic history."""
    from .main import run_tick  # local import avoids a cycle

    label_time = STARTS.get(start, STARTS["morning"])[1]
    demo_gateway.clear()
    rate_limiter.reset()
    clock = qs.runtime.clock
    if start == "early":
        clock.reset(datetime.combine(DEMO_START.date(), label_time), clock.speed)
        with session_scope() as s:
            simulator.reset(s, qs.now(), empty=True)
        return
    clock.reset(DEMO_START, clock.speed)
    clock.set_running(False)
    with session_scope() as s:
        simulator.reset(s, qs.now())
    target = datetime.combine(DEMO_START.date(), label_time)
    while clock.now() < target:
        clock.advance(1.0)
        run_tick()
    clock.set_running(True)
