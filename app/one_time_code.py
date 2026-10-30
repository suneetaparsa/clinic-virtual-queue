"""Web join protection: a 4-digit code texted to the phone before joining.

Closes the privacy leak where the join form told anyone who typed a phone
number whether that number was already in line. The page shown after the
form is identical whatever the number's status; only the phone's owner sees
the code, and only after entering it do they learn they are already in line.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import queue_service as qs
from . import security
from .config import settings
from .models import Clinic, OneTimeCode, QueueEntry

CODE_VALID_MINUTES = 10
MAX_SENDS = 3


@dataclass
class VerifyResult:
    outcome: str  # joined | existing | bad | tries | expired | full | used
    entry: QueueEntry | None = None
    token: str | None = None


def _send(s: Session, clinic: Clinic, otc: OneTimeCode, at: datetime) -> None:
    code = security.new_code()
    otc.code_hash = security.code_hash(code, otc.public_id)
    otc.created_at = at
    otc.tries = 0
    qs.send_text(s, clinic, security.decrypt_phone(otc.phone_cipher), "code", otc.lang, at, code=code)


def start(s: Session, clinic: Clinic, first_name: str, birth_year: int, digits: str, lang: str, at: datetime) -> OneTimeCode:
    otc = OneTimeCode(
        public_id=secrets.token_urlsafe(18),
        clinic_id=clinic.id,
        phone_hash=security.phone_hash(digits),
        phone_cipher=security.encrypt_phone(digits),
        first_name=first_name.strip()[:40],
        birth_year=birth_year,
        lang=lang,
        code_hash="",
        created_at=at,
    )
    s.add(otc)
    s.flush()
    _send(s, clinic, otc, at)
    return otc


def get(s: Session, public_id: str) -> OneTimeCode | None:
    return s.scalars(select(OneTimeCode).where(OneTimeCode.public_id == public_id)).first()


def resend(s: Session, clinic: Clinic, otc: OneTimeCode, at: datetime) -> bool:
    if otc.used or otc.sends >= MAX_SENDS:
        return False
    otc.sends += 1
    _send(s, clinic, otc, at)
    return True


def verify(s: Session, clinic: Clinic, otc: OneTimeCode, code: str, at: datetime) -> VerifyResult:
    if otc.used:
        return VerifyResult("used")
    if otc.tries >= settings.code_tries:
        return VerifyResult("tries")
    if at - otc.created_at > timedelta(minutes=CODE_VALID_MINUTES):
        return VerifyResult("expired")
    if not secrets.compare_digest(security.code_hash((code or "").strip(), otc.public_id), otc.code_hash):
        otc.tries += 1
        return VerifyResult("tries" if otc.tries >= settings.code_tries else "bad")
    digits = security.decrypt_phone(otc.phone_cipher)
    existing = qs.active_entry_for_phone(s, clinic, at.date(), digits)
    if existing:
        # The owner proved the phone is theirs, so it is safe to show their place.
        # Only a hash of the page token is stored, so issue a fresh one (the old link stops working).
        otc.used = True
        token = security.new_token()
        existing.token_hash = security.token_hash(token)
        return VerifyResult("existing", existing, token)
    if qs.line_is_full(s, clinic, at):
        return VerifyResult("full")
    result = qs.join(s, clinic, otc.first_name, otc.birth_year, digits, otc.lang, "web", at)
    if result.error:
        return VerifyResult(result.error)
    otc.used = True
    return VerifyResult("joined", result.entry, result.token)
