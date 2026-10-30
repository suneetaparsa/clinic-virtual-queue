"""Text-message and voice webhooks (Twilio), plus the on-screen demo phone."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import queue_service as qs
from . import security, text_commands
from .config import settings
from .db import session_scope
from .messaging import demo_gateway, twilio_signature_valid
from .web import DEMO_PHONE_COOKIE, templates

router = APIRouter()
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


async def _check_twilio(request: Request) -> dict[str, str]:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if settings.twilio_enabled:
        url = (settings.public_base_url.rstrip("/") + request.url.path) if settings.public_base_url else str(request.url)
        if not twilio_signature_valid(url, form, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(status_code=403, detail="bad signature")
    return form


@router.post("/hooks/text")
async def inbound_text(request: Request):
    form = await _check_twilio(request)
    digits = security.normalize_us_phone(form.get("From", ""))
    if digits:
        at = qs.now()
        with session_scope() as s:
            text_commands.handle(s, qs.get_clinic(s), digits, form.get("Body", ""), at)
    return Response(EMPTY_TWIML, media_type="application/xml")


@router.post("/hooks/voice")
async def inbound_call(request: Request):
    await _check_twilio(request)
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Gather numDigits="1" action="/hooks/voice/gather" method="POST">'
        "<Say>Sunrise Community Clinic. To keep your place in line, press 1.</Say>"
        '<Say language="es-MX">Para mantener su lugar en la fila, marque 1.</Say>'
        "</Gather><Say>Goodbye.</Say></Response>"
    )
    return Response(twiml, media_type="application/xml")


@router.post("/hooks/voice/gather")
async def inbound_call_digit(request: Request):
    form = await _check_twilio(request)
    digits = security.normalize_us_phone(form.get("From", ""))
    ok = False
    if digits and form.get("Digits") == "1":
        at = qs.now()
        with session_scope() as s:
            ok = text_commands.handle_voice_press_one(s, qs.get_clinic(s), digits, at)
    say = "Thank you. Your place is confirmed." if ok else "There is nothing to confirm for this number."
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response><Say>{say}</Say></Response>', media_type="application/xml")


# ---------------------------------------------------------------- demo phone
def _demo_only():
    if not settings.demo_mode:
        raise HTTPException(status_code=404)


@router.get("/demo/phone", response_class=HTMLResponse)
def demo_phone(request: Request):
    _demo_only()
    digits = security.normalize_us_phone(request.cookies.get(DEMO_PHONE_COOKIE, "") or "")
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        mine = qs.active_entry_for_phone(s, clinic, qs.now().date(), digits) if digits else None
        awaiting = bool(mine and mine.status == "awaiting")
    ctx = {
        "request": request,
        "digits": digits,
        "pretty": security.format_phone(digits) if digits else "",
        "thread": demo_gateway.thread(digits) if digits else [],
        "clinic": clinic,
        "awaiting": awaiting,
        "in_line": bool(mine),
    }
    resp = templates.TemplateResponse(request, "demo_phone.html", ctx)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/demo/phone/number")
def demo_phone_number(number: str = Form("")):
    _demo_only()
    digits = security.normalize_us_phone(number)
    resp = RedirectResponse("/demo/phone", status_code=303)
    if digits:
        resp.set_cookie(DEMO_PHONE_COOKIE, digits, max_age=60 * 60 * 24, samesite="lax")
    else:
        resp.delete_cookie(DEMO_PHONE_COOKIE)
    return resp


@router.post("/demo/phone/send")
def demo_phone_send(request: Request, body: str = Form("")):
    _demo_only()
    digits = security.normalize_us_phone(request.cookies.get(DEMO_PHONE_COOKIE, "") or "")
    if digits and body.strip():
        at = qs.now()
        demo_gateway.record_inbound(digits, body.strip()[:300], at)
        with session_scope() as s:
            text_commands.handle(s, qs.get_clinic(s), digits, body, at)
    return RedirectResponse("/demo/phone#end", status_code=303)


@router.post("/demo/phone/call")
def demo_phone_call(request: Request):
    _demo_only()
    digits = security.normalize_us_phone(request.cookies.get(DEMO_PHONE_COOKIE, "") or "")
    if digits:
        at = qs.now()
        demo_gateway.record_inbound(digits, "(Called the clinic and pressed 1)", at)
        with session_scope() as s:
            text_commands.handle_voice_press_one(s, qs.get_clinic(s), digits, at)
    return RedirectResponse("/demo/phone#end", status_code=303)
