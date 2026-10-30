"""Shared helpers for the page routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import settings
from .i18n import PAGE, clock_text

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["clock_text"] = clock_text
templates.env.globals["demo_mode"] = settings.demo_mode
templates.env.globals["default_pin"] = settings.staff_pin == "2468"

LANG_COOKIE = "lang"
PLACE_COOKIE = "my_place"
DEMO_PHONE_COOKIE = "demo_phone"
REFRESH_SECONDS = 15 if settings.demo_mode else 60


def pick_lang(request: Request) -> str:
    q = request.query_params.get("lang")
    if q in PAGE:
        return q
    c = request.cookies.get(LANG_COOKIE)
    return c if c in PAGE else "en"


def client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def no_store(response):
    """Patient pages must not be cached: a shared phone's back button must not show someone's place."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
