"""Database tables. Personal fields live only on QueueEntry and are purged
48 hours after the visit day (see queue_service.purge_personal_data)."""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# Queue statuses, in the order a patient normally moves through them.
WAITING = "waiting"
AWAITING = "awaiting"  # confirmation text sent, waiting for Y
CONFIRMED = "confirmed"
ARRIVED = "arrived"
IN_ROOM = "in_room"
COMPLETED = "completed"
LEFT = "left"
REMOVED = "removed"

IN_LINE = (WAITING, AWAITING, CONFIRMED, ARRIVED)


class Clinic(Base):
    __tablename__ = "clinic"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    name_es: Mapped[str] = mapped_column(String(120))
    phone_display: Mapped[str] = mapped_column(String(20))
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)
    line_opens: Mapped[time] = mapped_column(Time, default=time(7, 30))  # patients may join from here
    last_join: Mapped[time] = mapped_column(Time, default=time(16, 30))  # no new check-ins after this
    open_weekdays: Mapped[str] = mapped_column(String(7), default="0123456")  # 0 = Monday
    providers_working: Mapped[int] = mapped_column(Integer, default=2)
    accepting: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hourly_cap_per_provider: Mapped[int] = mapped_column(Integer, default=12)


class QueueEntry(Base):
    __tablename__ = "queue_entry"
    __table_args__ = (UniqueConstraint("clinic_id", "day", "line_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinic.id"))
    day: Mapped[date] = mapped_column(Date, index=True)
    line_id: Mapped[str] = mapped_column(String(8))
    position_key: Mapped[int] = mapped_column(Integer)  # order of joining
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Personal fields (purged after retention window).
    first_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phone_cipher: Mapped[str | None] = mapped_column(String(300), nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    purged: Mapped[bool] = mapped_column(Boolean, default=False)

    lang: Mapped[str] = mapped_column(String(2), default="en")
    channel: Mapped[str] = mapped_column(String(8), default="web")  # web | text
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default=WAITING, index=True)

    joined_at: Mapped[datetime] = mapped_column(DateTime)
    est_call_at_join: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    est_call_at_confirm: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    room_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Simulation-only fields (unused in production).
    sim_visit_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    sim_reply: Mapped[str | None] = mapped_column(String(4), nullable=True)
    sim_reply_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sim_arrive_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sim_leave_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def in_line(self) -> bool:
        return self.status in IN_LINE


class QueueEvent(Base):
    __tablename__ = "queue_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("queue_entry.id"), index=True)
    event: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(12))  # patient | staff | system | record_feed
    at: Mapped[datetime] = mapped_column(DateTime)


class VisitDuration(Base):
    """Anonymous visit lengths, kept permanently to train the estimator."""

    __tablename__ = "visit_duration"
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinic.id"))
    day: Mapped[date] = mapped_column(Date, index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    hour: Mapped[int] = mapped_column(Integer)
    providers: Mapped[int] = mapped_column(Integer)
    minutes: Mapped[float] = mapped_column(Float)


class HourlyStat(Base):
    """One row per clinic, day and hour: feeds the front desk weekly pattern."""

    __tablename__ = "hourly_stat"
    __table_args__ = (UniqueConstraint("clinic_id", "day", "hour"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinic.id"))
    day: Mapped[date] = mapped_column(Date, index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday
    hour: Mapped[int] = mapped_column(Integer)
    arrivals: Mapped[int] = mapped_column(Integer)
    providers: Mapped[float] = mapped_column(Float)
    consult_minutes: Mapped[float] = mapped_column(Float)
    avg_wait_minutes: Mapped[float] = mapped_column(Float)


class OneTimeCode(Base):
    """A pending web join waiting for the code texted to the phone."""

    __tablename__ = "one_time_code"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinic.id"))
    phone_hash: Mapped[str] = mapped_column(String(64))
    phone_cipher: Mapped[str] = mapped_column(String(300))
    first_name: Mapped[str] = mapped_column(String(40))
    birth_year: Mapped[int] = mapped_column(Integer)
    lang: Mapped[str] = mapped_column(String(2))
    code_hash: Mapped[str] = mapped_column(String(64))
    tries: Mapped[int] = mapped_column(Integer, default=0)
    sends: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class MessageLog(Base):
    """Delivery log. Message bodies are never stored."""

    __tablename__ = "message_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int | None] = mapped_column(ForeignKey("queue_entry.id"), nullable=True)
    phone_hash: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    template: Mapped[str] = mapped_column(String(30))
    at: Mapped[datetime] = mapped_column(DateTime)


class OptOut(Base):
    __tablename__ = "opt_out"
    phone_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime)
