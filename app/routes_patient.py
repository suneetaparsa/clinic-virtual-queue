"""Patient web pages: no login, server-rendered, work without JavaScript."""
from __future__ import annotations

import io
import re

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import Markup

from . import one_time_code as otc_service
from . import queue_service as qs
from . import security
from .config import settings
from .db import session_scope
from .i18n import PAGE, clock_text, hours_words
from .models import IN_LINE
from .web import LANG_COOKIE, PLACE_COOKIE, REFRESH_SECONDS, client_key, no_store, pick_lang, templates

router = APIRouter()
NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ'’\- ]{1,30}$")


def _base(request: Request, clinic, lang: str, **extra) -> dict:
    at = qs.now()
    words = hours_words(clinic, at, lang)
    ph = qs.phase(clinic, at)
    return {
        "request": request,
        "L": PAGE[lang],
        "lang": lang,
        "other_lang": "es" if lang == "en" else "en",
        "clinic": clinic,
        "clinic_name": clinic.name_es if lang == "es" else clinic.name,
        "now_text": clock_text(at),
        "phase": ph,
        "phase_text": PAGE[lang]["phase"][ph].format(**words),
        "can_join": ph in ("early", "open"),
        "show_wait": ph in ("early", "open", "last"),
        "words": words,
        "refresh": REFRESH_SECONDS,
        **extra,
    }


def _render(name: str, ctx: dict, lang: str, status_code: int = 200):
    resp = templates.TemplateResponse(ctx["request"], name, ctx, status_code=status_code)
    resp.set_cookie(LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return no_store(resp)


def _qr_svg(token: str) -> Markup:
    img = qrcode.make("SCQ1:" + token, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
    return Markup(svg)


@router.get("/", include_in_schema=False)
def root():
    with session_scope() as s:
        slug = qs.get_clinic(s).slug
    return RedirectResponse(f"/c/{slug}", status_code=303)


@router.get("/c/{slug}", response_class=HTMLResponse)
def home(request: Request, slug: str):
    lang = pick_lang(request)
    at = qs.now()
    with session_scope() as s:
        clinic = qs.get_clinic(s, slug)
        general = qs.estimate_for(s, clinic, None, at)
        waiting = len(qs.lineup(s, clinic, at.date()))
        mine = None
        token = request.cookies.get(PLACE_COOKIE)
        if token:
            e = qs.entry_by_token(s, token)
            if e and e.in_line and e.day == at.date():
                mine = {"line_id": e.line_id, "est": qs.estimate_for(s, clinic, e, at), "token": token}
        ctx = _base(request, clinic, lang, general=general, waiting=waiting, mine=mine, full=qs.line_is_full(s, clinic, at))
        return _render("home.html", ctx, lang)


def _join_form(request, clinic, lang, form=None, error=None, status_code=200):
    ctx = _base(request, clinic, lang, form=form or {"name": "", "year": "", "phone": "", "consent": False}, error=error, max_year=qs.now().year)
    return _render("join.html", ctx, lang, status_code)


@router.get("/c/{slug}/join", response_class=HTMLResponse)
def join_form(request: Request, slug: str):
    lang = pick_lang(request)
    with session_scope() as s:
        return _join_form(request, qs.get_clinic(s, slug), lang)


@router.post("/c/{slug}/join", response_class=HTMLResponse)
def join_submit(
    request: Request,
    slug: str,
    name: str = Form(""),
    year: str = Form(""),
    phone: str = Form(""),
    consent: str | None = Form(None),
    switch_lang: str | None = Form(None),
):
    lang = switch_lang if switch_lang in PAGE else pick_lang(request)
    form = {"name": name, "year": year, "phone": phone, "consent": bool(consent)}
    at = qs.now()
    with session_scope() as s:
        clinic = qs.get_clinic(s, slug)
        if switch_lang:
            return _join_form(request, clinic, lang, form)
        L = PAGE[lang]
        digits = security.normalize_us_phone(phone)
        year_ok = year.strip().isdigit() and len(year.strip()) == 4 and 1900 <= int(year) <= at.year
        if not name.strip() or not NAME_RE.match(name.strip()):
            return _join_form(request, clinic, lang, form, ("name", L["e_name"]), 422)
        if not year_ok:
            return _join_form(request, clinic, lang, form, ("year", L["e_year"].format(year=at.year)), 422)
        if not digits:
            return _join_form(request, clinic, lang, form, ("phone", L["e_phone"]), 422)
        if not consent:
            return _join_form(request, clinic, lang, form, ("consent", L["e_consent"]), 422)
        if not qs.can_join(clinic, at):
            return _join_form(request, clinic, lang, form, ("form", L["e_closed"].format(**hours_words(clinic, at, lang))), 409)
        if not security.rate_limiter.allow("join:" + client_key(request), settings.join_attempts_per_10_min, 600):
            return _join_form(request, clinic, lang, form, ("form", L["e_rate"]), 429)

        if settings.join_one_time_code:
            # Same response whatever the number's status: nothing here reveals who is in line.
            otc = otc_service.start(s, clinic, name.strip(), int(year), digits, lang, at)
            return RedirectResponse(f"/c/{slug}/verify/{otc.public_id}", status_code=303)

        # Legacy flow (JOIN_ONE_TIME_CODE=false): kept only to demonstrate the privacy leak.
        if qs.line_is_full(s, clinic, at):
            return _join_form(request, clinic, lang, form, ("form", L["line_full"]), 409)
        result = qs.join(s, clinic, name.strip(), int(year), digits, lang, "web", at)
        if result.error == "duplicate":
            return _join_form(request, clinic, lang, form, ("phone", L["e_dup"]), 409)
        if result.error in ("full", "closed"):
            msg = L["line_full"] if result.error == "full" else L["e_closed"].format(**hours_words(clinic, at, lang))
            return _join_form(request, clinic, lang, form, ("form", msg), 409)
        return _to_place(result.token)


def _to_place(token: str):
    resp = RedirectResponse(f"/q/{token}", status_code=303)
    resp.set_cookie(PLACE_COOKIE, token, max_age=60 * 60 * 18, httponly=True, samesite="lax")
    return resp


def _verify_page(request, clinic, lang, otc, error=None, note=None, status_code=200):
    digits = security.decrypt_phone(otc.phone_cipher)
    ctx = _base(request, clinic, lang, otc=otc, masked=security.mask_phone(digits), error=error, note=note,
                can_resend=otc.sends < otc_service.MAX_SENDS, locked=otc.tries >= settings.code_tries)
    return _render("verify.html", ctx, lang, status_code)


@router.get("/c/{slug}/verify/{public_id}", response_class=HTMLResponse)
def verify_form(request: Request, slug: str, public_id: str):
    with session_scope() as s:
        clinic = qs.get_clinic(s, slug)
        otc = otc_service.get(s, public_id)
        if otc is None or otc.used:
            return RedirectResponse(f"/c/{slug}", status_code=303)
        return _verify_page(request, clinic, otc.lang, otc)


@router.post("/c/{slug}/verify/{public_id}", response_class=HTMLResponse)
def verify_submit(request: Request, slug: str, public_id: str, code: str = Form("")):
    at = qs.now()
    with session_scope() as s:
        clinic = qs.get_clinic(s, slug)
        otc = otc_service.get(s, public_id)
        if otc is None:
            return RedirectResponse(f"/c/{slug}", status_code=303)
        L = PAGE[otc.lang]
        r = otc_service.verify(s, clinic, otc, code, at)
        if r.outcome in ("joined", "existing"):
            return _to_place(r.token)
        msg = {"bad": L["e_code"], "tries": L["e_tries"], "expired": L["e_expired"], "full": L["line_full"], "used": L["e_expired"],
               "closed": L["e_closed"].format(**hours_words(clinic, at, otc.lang)), "duplicate": L["e_expired"]}[r.outcome]
        return _verify_page(request, clinic, otc.lang, otc, error=msg, status_code=422)


@router.post("/c/{slug}/verify/{public_id}/resend", response_class=HTMLResponse)
def verify_resend(request: Request, slug: str, public_id: str):
    at = qs.now()
    with session_scope() as s:
        clinic = qs.get_clinic(s, slug)
        otc = otc_service.get(s, public_id)
        if otc is None or otc.used:
            return RedirectResponse(f"/c/{slug}", status_code=303)
        sent = otc_service.resend(s, clinic, otc, at)
        L = PAGE[otc.lang]
        return _verify_page(request, clinic, otc.lang, otc, note=L["v_sent"] if sent else None, error=None if sent else L["e_tries"])


@router.get("/q/{token}", response_class=HTMLResponse)
def place(request: Request, token: str, confirm_leave: int = 0):
    at = qs.now()
    with session_scope() as s:
        e = qs.entry_by_token(s, token)
        clinic = qs.get_clinic(s)
        if e is None or e.day != at.date():
            lang = pick_lang(request)
            ctx = _base(request, clinic, lang, title=PAGE[lang]["expired"])
            return _render("expired.html", ctx, lang, 404)
        lang = request.query_params.get("lang") if request.query_params.get("lang") in PAGE else e.lang
        line = qs.lineup(s, clinic, at.date())
        position = next((i + 1 for i, x in enumerate(line) if x.id == e.id), None)
        ctx = _base(
            request, clinic, lang, e=e, token=token, active=e.status in IN_LINE, position=position,
            est=qs.estimate_for(s, clinic, e, at), qr=_qr_svg(token) if e.status in IN_LINE else None,
            confirm_leave=bool(confirm_leave),
        )
        resp = _render("place.html", ctx, lang)
        resp.set_cookie(PLACE_COOKIE, token, max_age=60 * 60 * 18, httponly=True, samesite="lax")
        return resp


@router.post("/q/{token}/leave")
def place_leave(token: str):
    at = qs.now()
    with session_scope() as s:
        e = qs.entry_by_token(s, token)
        if e is not None:
            clinic = qs.get_clinic(s)
            if qs.leave(s, clinic, e, at, "web"):
                qs.text_entry(s, clinic, e, "left", at)
    return RedirectResponse(f"/q/{token}", status_code=303)
