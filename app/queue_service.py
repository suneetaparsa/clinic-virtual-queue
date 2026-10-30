"""Queue rules: joining, confirmation, arrival, rooms, removal and purging.

Every function takes an open session and the clinic's current time, so the
same code runs on the real clock and on the demo's simulated clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import security
from .config import settings
from .estimator import Estimate, estimate
from .i18n import TEXT, clock_text
from .messaging import gateway
from .models import (
    ARRIVED,
    AWAITING,
    COMPLETED,
    CONFIRMED,
    IN_LINE,
    IN_ROOM,
    LEFT,
    REMOVED,
    WAITING,
    Clinic,
    MessageLog,
    OptOut,
    QueueEntry,
    QueueEvent,
    VisitDuration,
)


class Runtime:
    """Process-wide values set at startup (clock) and on each request (base URL)."""

    clock = None  # app.clock.Clock
    base_url: str = "http://localhost:8000"


runtime = Runtime()


def now() -> datetime:
    return runtime.clock.now()


# ---------------------------------------------------------------- lookups
def get_clinic(s: Session, slug: str | None = None) -> Clinic:
    q = select(Clinic)
    if slug:
        q = q.where(Clinic.slug == slug)
    clinic = s.scalars(q.limit(1)).first()
    if clinic is None:
        raise LookupError("clinic not found")
    return clinic


def lineup(s: Session, clinic: Clinic, day: date) -> list[QueueEntry]:
    return list(
        s.scalars(
            select(QueueEntry)
            .where(QueueEntry.clinic_id == clinic.id, QueueEntry.day == day, QueueEntry.status.in_(IN_LINE))
            .order_by(QueueEntry.position_key)
        )
    )


def in_rooms(s: Session, clinic: Clinic, day: date) -> list[QueueEntry]:
    return list(
        s.scalars(
            select(QueueEntry)
            .where(QueueEntry.clinic_id == clinic.id, QueueEntry.day == day, QueueEntry.status == IN_ROOM)
            .order_by(QueueEntry.room_at)
        )
    )


def recent_visit_minutes(s: Session, clinic: Clinic, day: date) -> list[float]:
    return list(
        s.scalars(
            select(VisitDuration.minutes)
            .where(VisitDuration.clinic_id == clinic.id, VisitDuration.day == day)
            .order_by(VisitDuration.id)
        )
    )


def entry_by_token(s: Session, token: str) -> QueueEntry | None:
    return s.scalars(select(QueueEntry).where(QueueEntry.token_hash == security.token_hash(token))).first()


def entry_by_line_id(s: Session, clinic: Clinic, day: date, line_id: str) -> QueueEntry | None:
    return s.scalars(
        select(QueueEntry).where(QueueEntry.clinic_id == clinic.id, QueueEntry.day == day, QueueEntry.line_id == line_id)
    ).first()


def active_entry_for_phone(s: Session, clinic: Clinic, day: date, digits: str) -> QueueEntry | None:
    return s.scalars(
        select(QueueEntry).where(
            QueueEntry.clinic_id == clinic.id,
            QueueEntry.day == day,
            QueueEntry.phone_hash == security.phone_hash(digits),
            QueueEntry.status.in_(IN_LINE),
        )
    ).first()


# ---------------------------------------------------------------- estimates
def estimate_for(s: Session, clinic: Clinic, entry: QueueEntry | None, at: datetime) -> Estimate:
    """Estimate for `entry`, or for someone joining now when `entry` is None."""
    day = at.date()
    line = lineup(s, clinic, day)
    ahead = [e for e in line if entry is None or e.position_key < entry.position_key]
    rooms = in_rooms(s, clinic, day)
    elapsed = [max(0.0, (at - e.room_at).total_seconds() / 60) for e in rooms if e.room_at]
    return estimate(
        [e.status for e in ahead], elapsed, recent_visit_minutes(s, clinic, day), clinic.providers_working,
        start_delay_minutes=minutes_until_doctors(clinic, at),
    )


def est_call_time(s: Session, clinic: Clinic, entry: QueueEntry, at: datetime) -> datetime:
    return at + timedelta(minutes=estimate_for(s, clinic, entry, at).mid)


# ---------------------------------------------------------------- texts
def _opted_out(s: Session, phash: str) -> bool:
    return s.get(OptOut, phash) is not None


def send_text(s: Session, clinic: Clinic, digits: str, key: str, lang: str, at: datetime, entry_id: int | None = None, **fmt) -> bool:
    phash = security.phone_hash(digits)
    if key not in {"stop", "start"} and _opted_out(s, phash):
        return False
    name = clinic.name_es if lang == "es" else clinic.name
    body = TEXT[lang][key].format(clinic=name, phone=clinic.phone_display, home=f"{runtime.base_url}/c/{clinic.slug}", **fmt)
    gateway.send(digits, body, at)
    s.add(MessageLog(entry_id=entry_id, phone_hash=phash, direction="out", template=key, at=at))
    return True


def text_entry(s: Session, clinic: Clinic, entry: QueueEntry, key: str, at: datetime, **fmt) -> None:
    if entry.synthetic or not entry.phone_cipher:
        return
    send_text(s, clinic, security.decrypt_phone(entry.phone_cipher), key, entry.lang, at, entry.id, **fmt)


def _event(s: Session, entry: QueueEntry, event: str, actor: str, at: datetime) -> None:
    s.add(QueueEvent(entry_id=entry.id, event=event, actor=actor, at=at))


# ---------------------------------------------------------------- opening hours
def phase(clinic: Clinic, at: datetime) -> str:
    """before | early | open | last | after | closed_day.

    early = line open, doctors not started; last = no new check-ins, people in line still seen."""
    if str(at.weekday()) not in clinic.open_weekdays:
        return "closed_day"
    t = at.time()
    if t < clinic.line_opens:
        return "before"
    if t < clinic.opens:
        return "early"
    if t < clinic.last_join:
        return "open"
    if t < clinic.closes:
        return "last"
    return "after"


def can_join(clinic: Clinic, at: datetime) -> bool:
    return phase(clinic, at) in ("early", "open")


def next_open_day(clinic: Clinic, at: datetime) -> date:
    """Today if the line has not opened yet, otherwise the next open weekday."""
    if phase(clinic, at) == "before":
        return at.date()
    d = at.date() + timedelta(days=1)
    for _ in range(7):
        if str(d.weekday()) in clinic.open_weekdays:
            return d
        d += timedelta(days=1)
    return d


def minutes_until_doctors(clinic: Clinic, at: datetime) -> float:
    start = datetime.combine(at.date(), clinic.opens)
    return max(0.0, (start - at).total_seconds() / 60)


# ---------------------------------------------------------------- joining
@dataclass
class JoinResult:
    entry: QueueEntry | None = None
    token: str | None = None
    error: str | None = None  # "duplicate" | "full" | "closed"


def line_is_full(s: Session, clinic: Clinic, at: datetime) -> bool:
    if not clinic.accepting:
        return True
    hour_ago = at - timedelta(hours=1)
    joined = s.scalar(
        select(func.count()).select_from(QueueEntry).where(
            QueueEntry.clinic_id == clinic.id, QueueEntry.joined_at > hour_ago, QueueEntry.joined_at <= at
        )
    )
    return joined >= clinic.hourly_cap_per_provider * max(1, clinic.providers_working)


def _unique_line_id(s: Session, clinic: Clinic, day: date) -> str:
    for _ in range(200):
        lid = security.new_line_id()
        if entry_by_line_id(s, clinic, day, lid) is None:
            return lid
    raise RuntimeError("could not allocate a line ID")


def create_entry(
    s: Session,
    clinic: Clinic,
    first_name: str,
    birth_year: int,
    digits: str | None,
    lang: str,
    channel: str,
    at: datetime,
    synthetic: bool = False,
) -> tuple[QueueEntry, str]:
    day = at.date()
    last_key = s.scalar(select(func.max(QueueEntry.position_key)).where(QueueEntry.clinic_id == clinic.id, QueueEntry.day == day))
    token = security.new_token()
    entry = QueueEntry(
        clinic_id=clinic.id,
        day=day,
        line_id=_unique_line_id(s, clinic, day),
        position_key=(last_key or 0) + 1,
        token_hash=security.token_hash(token),
        first_name=first_name.strip()[:40],
        birth_year=birth_year,
        phone_cipher=security.encrypt_phone(digits) if digits else None,
        phone_hash=security.phone_hash(digits) if digits else None,
        lang=lang,
        channel=channel,
        synthetic=synthetic,
        status=WAITING,
        joined_at=at,
    )
    s.add(entry)
    s.flush()
    entry.est_call_at_join = est_call_time(s, clinic, entry, at)
    _event(s, entry, "joined", "patient", at)
    return entry, token


def join(s: Session, clinic: Clinic, first_name: str, birth_year: int, digits: str, lang: str, channel: str, at: datetime) -> JoinResult:
    if active_entry_for_phone(s, clinic, at.date(), digits):
        return JoinResult(error="duplicate")
    if not can_join(clinic, at):
        return JoinResult(error="closed")
    if line_is_full(s, clinic, at):
        return JoinResult(error="full")
    entry, token = create_entry(s, clinic, first_name, birth_year, digits, lang, channel, at)
    est = estimate_for(s, clinic, entry, at)
    if channel == "web":
        # Ticking the consent box on the web form turns texts back on.
        opt_out = s.get(OptOut, entry.phone_hash)
        if opt_out is not None:
            s.delete(opt_out)
            s.flush()
    text_entry(s, clinic, entry, "checkin", at, line_id=entry.line_id, range=est.text(lang), t=clock_text(at), link=f"{runtime.base_url}/q/{token}")
    return JoinResult(entry=entry, token=token)


# ---------------------------------------------------------------- transitions
def confirm(s: Session, entry: QueueEntry, at: datetime, actor: str = "patient") -> bool:
    if entry.status != AWAITING:
        return False
    entry.status = CONFIRMED
    entry.confirmed_at = at
    _event(s, entry, "confirmed", actor, at)
    return True


def end(s: Session, entry: QueueEntry, status: str, reason: str, actor: str, at: datetime) -> bool:
    if entry.status not in IN_LINE:
        return False
    entry.status = status
    entry.end_reason = reason
    entry.ended_at = at
    _event(s, entry, f"{status}:{reason}", actor, at)
    return True


def arrive(s: Session, entry: QueueEntry, at: datetime, actor: str = "staff") -> bool:
    if entry.status not in (WAITING, AWAITING, CONFIRMED):
        return False
    entry.status = ARRIVED
    entry.arrived_at = at
    _event(s, entry, "arrived", actor, at)
    return True


def call_to_room(s: Session, entry: QueueEntry, at: datetime, actor: str = "staff") -> bool:
    if entry.status != ARRIVED:
        return False
    entry.status = IN_ROOM
    entry.room_at = at
    _event(s, entry, "in_room", actor, at)
    return True


def complete(s: Session, clinic: Clinic, entry: QueueEntry, at: datetime, actor: str = "staff") -> bool:
    if entry.status != IN_ROOM or entry.room_at is None:
        return False
    entry.status = COMPLETED
    entry.done_at = at
    minutes = max(1.0, (at - entry.room_at).total_seconds() / 60)
    s.add(
        VisitDuration(
            clinic_id=clinic.id, day=entry.day, weekday=entry.day.weekday(), hour=entry.room_at.hour,
            providers=clinic.providers_working, minutes=minutes,
        )
    )
    _event(s, entry, "completed", actor, at)
    return True


def remove_by_staff(s: Session, clinic: Clinic, entry: QueueEntry, at: datetime) -> bool:
    if end(s, entry, REMOVED, "staff", "staff", at):
        text_entry(s, clinic, entry, "staff_removed", at)
        return True
    return False


def leave(s: Session, clinic: Clinic, entry: QueueEntry, at: datetime, how: str) -> bool:
    return end(s, entry, LEFT, how, "patient", at)


# ---------------------------------------------------------------- scheduler
def tick(s: Session, clinic: Clinic, at: datetime) -> None:
    """Runs every few seconds: confirmation texts, reminders and removals."""
    for entry in lineup(s, clinic, at.date()):
        if entry.status == WAITING:
            est = estimate_for(s, clinic, entry, at)
            if est.mid <= settings.confirm_at_minutes:
                entry.status = AWAITING
                entry.confirm_sent_at = at
                entry.est_call_at_confirm = at + timedelta(minutes=est.mid)
                _event(s, entry, "confirmation_sent", "system", at)
                text_entry(s, clinic, entry, "confirm", at, range=est.text(entry.lang), t=clock_text(at))
        elif entry.status == AWAITING and entry.confirm_sent_at and not entry.synthetic:
            waited = (at - entry.confirm_sent_at).total_seconds() / 60
            if waited >= settings.reply_window_minutes:
                end(s, entry, REMOVED, "no_reply", "system", at)
                text_entry(s, clinic, entry, "removed", at)
            elif waited >= settings.reminder_after_minutes and not entry.reminded:
                entry.reminded = True
                text_entry(s, clinic, entry, "remind", at)


def next_to_call(s: Session, clinic: Clinic, at: datetime) -> QueueEntry | None:
    """First patient at the desk, when a room is free. Unconfirmed patients are
    passed over (not removed) until their reply window closes."""
    if len(in_rooms(s, clinic, at.date())) >= clinic.providers_working:
        return None
    for e in lineup(s, clinic, at.date()):
        if e.status == ARRIVED:
            return e
    return None


# ---------------------------------------------------------------- privacy
def purge_personal_data(s: Session, at: datetime) -> int:
    cutoff_day = (at - timedelta(hours=settings.retention_hours_after_visit_day)).date()
    rows = list(s.scalars(select(QueueEntry).where(QueueEntry.day < cutoff_day, QueueEntry.purged.is_(False))))
    for e in rows:
        e.first_name = None
        e.birth_year = None
        e.phone_cipher = None
        e.phone_hash = None
        e.purged = True
    return len(rows)
