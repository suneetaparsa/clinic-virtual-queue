"""Front desk console (staff sign in with a PIN)."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import analytics
from . import queue_service as qs
from . import security
from .db import session_scope
from .i18n import clock_text
from .web import templates

router = APIRouter(prefix="/staff")
STATUS_LABEL = {
    "waiting": "Waiting", "awaiting": "Awaiting reply", "confirmed": "Confirmed", "arrived": "Arrived",
    "in_room": "In room", "completed": "Complete", "removed": "Removed", "left": "Left",
}


def _signed_in(request: Request) -> bool:
    return security.staff_cookie_valid(request.cookies.get(security.STAFF_COOKIE))


def _back(**params) -> RedirectResponse:
    q = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    resp = RedirectResponse("/staff" + (f"?{q}" if q else ""), status_code=303)
    resp.set_cookie(security.STAFF_COOKIE, security.make_staff_cookie(), httponly=True, samesite="strict")
    return resp


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/staff/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "staff_login.html", {"request": request, "error": error})


@router.post("/login")
def login(request: Request, pin: str = Form("")):
    if not security.rate_limiter.allow("pin:" + (request.client.host if request.client else "?"), 10, 600):
        return RedirectResponse("/staff/login?error=Too+many+tries.+Wait+10+minutes.", status_code=303)
    if not security.pin_ok(pin):
        return RedirectResponse("/staff/login?error=That+PIN+is+not+right.", status_code=303)
    return _back()


@router.get("/logout")
def logout():
    resp = RedirectResponse("/staff/login", status_code=303)
    resp.delete_cookie(security.STAFF_COOKIE)
    return resp


@router.get("", response_class=HTMLResponse)
def console(request: Request, found: str = "", arm: str = "", msg: str = "", err: str = "", metric: str = "wait", d: int | None = None, h: int | None = None, privacy: int = 0):
    if not _signed_in(request):
        return _login_redirect()
    at = qs.now()
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        day = at.date()
        line = qs.lineup(s, clinic, day)
        rows = [(i + 1, e, qs.estimate_for(s, clinic, e, at)) for i, e in enumerate(line)]
        rooms = qs.in_rooms(s, clinic, day)
        found_entry = qs.entry_by_line_id(s, clinic, day, found) if found else None
        sel = (d, h) if d is not None and h is not None else None
        ctx = {
            "request": request,
            "clinic": clinic,
            "at": at,
            "rows": rows,
            "rooms": rooms,
            "ready": qs.next_to_call(s, clinic, at),
            "found": found_entry,
            "arm": arm,
            "msg": msg,
            "err": err,
            "privacy": bool(privacy),
            "awaiting_count": sum(1 for e in line if e.status == "awaiting"),
            "today": analytics.today(s, clinic, at),
            "weekly": analytics.weekly(s, clinic, at, metric, sel),
            "acc": analytics.accuracy(s, clinic, at),
            "A": analytics,
            "STATUS_LABEL": STATUS_LABEL,
        }
        resp = templates.TemplateResponse(request, "staff.html", ctx)
    resp.headers["Cache-Control"] = "no-store"
    resp.set_cookie(security.STAFF_COOKIE, security.make_staff_cookie(), httponly=True, samesite="strict")
    return resp


@router.post("/lookup")
def lookup(request: Request, query: str = Form("")):
    if not _signed_in(request):
        return _login_redirect()
    at = qs.now()
    query = query.strip()
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        entry = None
        if query.upper().startswith("SCQ1:"):  # scanned code
            entry = qs.entry_by_token(s, query[5:])
            if entry is not None and entry.day != at.date():
                return _back(err=f"This code is from {entry.day:%A, %b} {entry.day.day}. Ask the patient to join today's line.")
        else:
            lid = security.normalize_line_id(query)
            entry = qs.entry_by_line_id(s, clinic, at.date(), lid) if lid else None
        if entry is None:
            shown = query if not query.upper().startswith("SCQ1:") else "that code"
            return _back(err=f"No patient in today's line with ID {shown.upper()}. Check the ID on the patient's screen.")
        if entry.status in ("left", "removed"):
            return _back(err=f"{entry.line_id} {'left' if entry.status == 'left' else 'was removed from'} the line at "
                             f"{clock_text(entry.ended_at)}. They can join again from their phone, at the back of the line.")
        return _back(found=entry.line_id)


def _act(request: Request, line_id: str, action: str, confirm: str = ""):
    if not _signed_in(request):
        return _login_redirect()
    at = qs.now()
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        e = qs.entry_by_line_id(s, clinic, at.date(), line_id)
        if e is None:
            return _back(err=f"{line_id} is not in today's line.")
        if action == "arrive" and qs.arrive(s, e, at):
            return _back(msg=f"{e.line_id} {e.first_name} marked arrived.")
        if action == "room" and qs.call_to_room(s, e, at):
            return _back(msg=f"{e.line_id} {e.first_name} called to a room.")
        if action == "complete" and qs.complete(s, clinic, e, at):
            return _back(msg=f"{e.line_id} visit complete.")
        if action == "remove":
            if confirm != "yes":
                return _back(arm=e.line_id)
            if qs.remove_by_staff(s, clinic, e, at):
                return _back(msg=f"{e.line_id} removed from the line. The patient was sent a text.")
        return _back(err=f"{e.line_id} is {STATUS_LABEL.get(e.status, e.status).lower()}; that action does not apply.")


@router.post("/entry/{line_id}/{action}")
def entry_action(request: Request, line_id: str, action: str, confirm: str = Form("")):
    return _act(request, line_id, action, confirm)


@router.post("/pause")
def pause(request: Request):
    if not _signed_in(request):
        return _login_redirect()
    at = qs.now()
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        clinic.accepting = not clinic.accepting
        clinic.paused_at = None if clinic.accepting else at
        msg = "New check-ins resumed." if clinic.accepting else "New check-ins paused. People already in line keep their place."
    return _back(msg=msg)


@router.post("/providers")
def providers(request: Request, change: int = Form(0)):
    if not _signed_in(request):
        return _login_redirect()
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        clinic.providers_working = max(1, min(12, clinic.providers_working + (1 if change > 0 else -1)))
        n = clinic.providers_working
    return _back(msg=f"Providers working set to {n}. Estimates updated.")

