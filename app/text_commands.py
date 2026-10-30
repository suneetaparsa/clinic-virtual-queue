"""Inbound text commands: WAIT, JOIN, LEAVE, Y/N, HELP, STOP/START (English and Spanish).

Every command acts only on the entry tied to the sending phone, and every
reply goes only to that phone.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import queue_service as qs
from . import security
from .i18n import clock_text, hours_words
from .models import AWAITING, Clinic, MessageLog, OptOut

EN = {"WAIT", "JOIN", "LEAVE", "HELP", "STOP", "Y", "YES", "OK", "START", "INFO"}
ES = {"ESPERA", "UNIRSE", "SALIR", "AYUDA", "PARAR", "SI", "S"}
STOP_WORDS = {"STOP", "PARAR", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
YES_WORDS = {"Y", "YES", "OK", "SI", "S"}
NAME_YEAR = re.compile(r"^\s*([A-Za-zÀ-ÿ'’\-]{1,30})\s+(\d{4})\s*$")

_pending_join: dict[str, tuple[str, datetime]] = {}
_pending_lock = threading.Lock()


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z0-9 ]", "", stripped.upper()).strip()


def reset_pending() -> None:
    with _pending_lock:
        _pending_join.clear()


def handle(s: Session, clinic: Clinic, digits: str, body: str, at: datetime) -> None:
    phash = security.phone_hash(digits)
    s.add(MessageLog(phone_hash=phash, direction="in", template="inbound", at=at))
    cmd = _normalize(body)
    day = at.date()
    mine = qs.active_entry_for_phone(s, clinic, day, digits)
    with _pending_lock:
        pending = _pending_join.get(phash)
        if pending and pending[1] < at:
            _pending_join.pop(phash, None)
            pending = None
    lang = "es" if cmd in ES else "en" if cmd in EN else (mine.lang if mine else (pending[0] if pending else "en"))

    def reply(key: str, **fmt) -> None:
        qs.send_text(s, clinic, digits, key, lang, at, mine.id if mine else None, **fmt)

    if cmd in STOP_WORDS:
        if s.get(OptOut, phash) is None:
            s.add(OptOut(phone_hash=phash, at=at))
            s.flush()
        reply("stop")
        with _pending_lock:
            _pending_join.pop(phash, None)
        return
    if cmd == "START":
        opt = s.get(OptOut, phash)
        if opt is not None:
            s.delete(opt)
            s.flush()
        reply("start")
        return
    if s.get(OptOut, phash) is not None:
        return  # opted out: no reply

    if pending and cmd not in {"WAIT", "ESPERA", "LEAVE", "SALIR", "HELP", "AYUDA"}:
        lang = pending[0]
        m = NAME_YEAR.match(body)
        if not m or not (1900 <= int(m.group(2)) <= at.year):
            reply("bad")
            return
        with _pending_lock:
            _pending_join.pop(phash, None)
        result = qs.join(s, clinic, m.group(1), int(m.group(2)), digits, lang, "text", at)
        if result.error == "duplicate":
            reply("dup")
        elif result.error == "closed":
            reply("closed", **hours_words(clinic, at, lang))
        elif result.error == "full":
            reply("full")
        return

    if cmd in {"WAIT", "ESPERA", "JOIN", "UNIRSE"} and not mine and not qs.can_join(clinic, at):
        if cmd in {"WAIT", "ESPERA"} and qs.phase(clinic, at) == "last":
            reply("wait", n=len(qs.lineup(s, clinic, day)), range=qs.estimate_for(s, clinic, None, at).text(lang), t=clock_text(at))
        reply("closed", **hours_words(clinic, at, lang))
        return

    if cmd in {"WAIT", "ESPERA"}:
        if mine:
            reply("status", line_id=mine.line_id, range=qs.estimate_for(s, clinic, mine, at).text(lang), t=clock_text(at))
        else:
            reply("wait", n=len(qs.lineup(s, clinic, day)), range=qs.estimate_for(s, clinic, None, at).text(lang), t=clock_text(at))
    elif cmd in {"JOIN", "UNIRSE"}:
        if mine:
            reply("dup")
        elif qs.line_is_full(s, clinic, at):
            reply("full")
        else:
            with _pending_lock:
                _pending_join[phash] = (lang, at + timedelta(minutes=30))
            reply("ask")
    elif cmd in {"LEAVE", "SALIR"}:
        if mine and qs.leave(s, clinic, mine, at, "text"):
            reply("left")
        else:
            reply("none")
    elif cmd in YES_WORDS:
        if mine and qs.confirm(s, mine, at):
            lang = mine.lang
            reply("confirmed")
        else:
            reply("nothing")
    elif cmd in {"N", "NO"}:
        if mine and mine.status == AWAITING and qs.leave(s, clinic, mine, at, "declined"):
            lang = mine.lang
            reply("declined")
        else:
            reply("nothing")
    elif cmd in {"HELP", "AYUDA", "INFO"}:
        reply("help")
    else:
        reply("unknown")


def handle_voice_press_one(s: Session, clinic: Clinic, digits: str, at: datetime) -> bool:
    """Caller pressed 1 on the confirmation line."""
    mine = qs.active_entry_for_phone(s, clinic, at.date(), digits)
    if mine and qs.confirm(s, mine, at, actor="patient"):
        qs.text_entry(s, clinic, mine, "confirmed", at)
        return True
    return False
