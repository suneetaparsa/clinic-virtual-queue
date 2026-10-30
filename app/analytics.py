"""Front desk numbers: today's dashboard, weekly pattern with a rule-based
explanation, and estimate accuracy."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import ARRIVED, IN_ROOM, LEFT, REMOVED, Clinic, HourlyStat, QueueEntry

HOURS = list(range(8, 17))
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
DAYS_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def hour_label(h: int) -> str:
    return "12 PM" if h == 12 else f"{h - 12} PM" if h > 12 else f"{h} AM"


def short_date(d: date) -> str:
    """'Sep 21' on every platform (strftime's %-d is not available on Windows)."""
    return f"{d:%b} {d.day}"


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


# ---------------------------------------------------------------- today
@dataclass
class Today:
    arrived: int
    started: int
    ended: int
    at_desk_now: int
    in_consult_now: int
    median_desk_wait: float | None
    median_consult: float | None
    left_or_removed: int
    hourly: list[tuple[int, int, int, int]]  # hour, arrived, started, ended
    hourly_max: int


def today(s: Session, clinic: Clinic, at: datetime) -> Today:
    rows = list(s.scalars(select(QueueEntry).where(QueueEntry.clinic_id == clinic.id, QueueEntry.day == at.date())))

    def seen(t):
        return t is not None and t <= at

    arrived = [e for e in rows if seen(e.arrived_at)]
    started = [e for e in rows if seen(e.room_at)]
    ended = [e for e in rows if seen(e.done_at)]
    hourly = []
    for h in HOURS:
        hourly.append(
            (
                h,
                sum(1 for e in arrived if e.arrived_at.hour == h),
                sum(1 for e in started if e.room_at.hour == h),
                sum(1 for e in ended if e.done_at.hour == h),
            )
        )
    top = max([4] + [max(a, b, c) for _, a, b, c in hourly])
    return Today(
        arrived=len(arrived),
        started=len(started),
        ended=len(ended),
        at_desk_now=sum(1 for e in rows if e.status == ARRIVED),
        in_consult_now=sum(1 for e in rows if e.status == IN_ROOM),
        median_desk_wait=_median([(e.room_at - e.arrived_at).total_seconds() / 60 for e in started if e.arrived_at]),
        median_consult=_median([(e.done_at - e.room_at).total_seconds() / 60 for e in ended]),
        left_or_removed=sum(1 for e in rows if e.status in (LEFT, REMOVED)),
        hourly=hourly,
        hourly_max=top + (top % 2),
    )


# ---------------------------------------------------------------- accuracy
@dataclass
class Accuracy:
    within_pct: int | None
    median_miss: float | None
    count: int
    rows: list[tuple[str, datetime, datetime, float]]  # line id, estimated, actual, miss minutes


def accuracy(s: Session, clinic: Clinic, at: datetime) -> Accuracy:
    rows = list(
        s.scalars(
            select(QueueEntry)
            .where(QueueEntry.clinic_id == clinic.id, QueueEntry.day == at.date(), QueueEntry.room_at.is_not(None), QueueEntry.est_call_at_join.is_not(None))
            .order_by(QueueEntry.room_at.desc())
        )
    )
    misses = [(e.room_at - e.est_call_at_join).total_seconds() / 60 for e in rows]
    within = sum(1 for m in misses if abs(m) <= settings.accuracy_band_minutes)
    return Accuracy(
        within_pct=round(100 * within / len(rows)) if rows else None,
        median_miss=_median([abs(m) for m in misses]),
        count=len(rows),
        rows=[(e.line_id, e.est_call_at_join, e.room_at, m) for e, m in list(zip(rows, misses))[:12]],
    )


# ---------------------------------------------------------------- weekly pattern
METRICS = {"wait": ("Average wait", "min"), "arrivals": ("Arrivals per hour", ""), "load": ("Provider load", "%")}


@dataclass
class CellStats:
    wait: float
    arrivals: float
    providers: float
    consult: float
    load: float


def _avg(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _stats(rows: list[HourlyStat]) -> CellStats:
    return CellStats(
        wait=_avg(r.avg_wait_minutes for r in rows),
        arrivals=_avg(r.arrivals for r in rows),
        providers=_avg(r.providers for r in rows),
        consult=_avg(r.consult_minutes for r in rows),
        load=_avg(r.arrivals * r.consult_minutes / (r.providers * 60) for r in rows if r.providers),
    )


@dataclass
class Weekly:
    weeks: list[date]
    grid: dict[tuple[int, int], float]
    lo: float
    hi: float
    metric: str
    sel: tuple[int, int]
    cell: CellStats | None
    peers: CellStats | None
    per_week: list[tuple[date, CellStats, float]]  # week start, cell stats, peer wait
    change_week: int | None
    finding: str


def weekly(s: Session, clinic: Clinic, at: datetime, metric: str = "wait", sel: tuple[int, int] | None = None, weeks_back: int = 8) -> Weekly:
    monday = at.date() - timedelta(days=at.weekday())
    first = monday - timedelta(weeks=weeks_back)
    rows = list(s.scalars(select(HourlyStat).where(HourlyStat.clinic_id == clinic.id, HourlyStat.day >= first, HourlyStat.day < monday)))
    weeks = [first + timedelta(weeks=i) for i in range(weeks_back)]
    by_cell: dict[tuple[int, int], list[HourlyStat]] = {}
    for r in rows:
        by_cell.setdefault((r.weekday, r.hour), []).append(r)
    metric = metric if metric in METRICS else "wait"
    grid = {}
    for d in range(5):
        for h in HOURS:
            st = _stats(by_cell.get((d, h), []))
            grid[(d, h)] = getattr(st, metric)
    if not rows:
        return Weekly(weeks, grid, 0, 0, metric, (1, 14), None, None, [], None, "No history yet. The pattern appears after the first full week.")
    if sel is None or sel not in grid:
        waits = {k: _stats(v).wait for k, v in by_cell.items() if k[0] < 5 and k[1] in HOURS}
        sel = max(waits, key=waits.get)
    d, h = sel
    cell = _stats(by_cell.get(sel, []))
    peer_rows = [r for (dd, hh), v in by_cell.items() if hh == h and dd != d and dd < 5 for r in v]
    peers = _stats(peer_rows)
    per_week = []
    for i, w in enumerate(weeks):
        wk = [r for r in by_cell.get(sel, []) if w <= r.day < w + timedelta(weeks=1)]
        pw = [r for r in peer_rows if w <= r.day < w + timedelta(weeks=1)]
        per_week.append((w, _stats(wk), _stats(pw).wait))
    change = None
    for i in range(1, len(per_week)):
        if abs(per_week[i][1].providers - per_week[i - 1][1].providers) >= 0.5:
            change = i
            break
    finding = _finding(d, h, cell, peers, per_week, change)
    vals = list(grid.values())
    return Weekly(weeks, grid, min(vals), max(vals), metric, sel, cell, peers, per_week, change, finding)


def _finding(d, h, c: CellStats, p: CellStats, per_week, change) -> str:
    head = f"{DAYS_FULL[d]} {hour_label(h)}: average wait {round(c.wait)} minutes versus {round(p.wait)} on other weekdays at this hour."
    reasons = []
    if p.arrivals:
        pct = (c.arrivals - p.arrivals) / p.arrivals * 100
        if pct >= 10:
            reasons.append(f"demand is higher: {c.arrivals:.1f} arrivals an hour, {round(pct)}% above the same hour on other weekdays")
        elif pct <= -10:
            reasons.append(f"demand is lower: {c.arrivals:.1f} arrivals an hour, {round(-pct)}% below other weekdays")
    if c.providers < p.providers - 0.25:
        reasons.append(f"capacity is lower: {c.providers:.1f} providers on duty on average versus {p.providers:.1f} on other weekdays")
    if p.consult and c.consult > p.consult * 1.1:
        reasons.append(f"consultations run longer: {round(c.consult)} minutes versus {round(p.consult)}")
    trend = ""
    if change:
        before = [x[1] for x in per_week[:change]]
        after = [x[1] for x in per_week[change:]]
        trend = (
            f" Providers at this hour changed from {round(_avg(b.providers for b in before))} to {round(_avg(a.providers for a in after))}"
            f" starting the week of {short_date(per_week[change][0])}; average wait went from"
            f" {round(_avg(b.wait for b in before))} to {round(_avg(a.wait for a in after))} minutes."
        )
    if not reasons:
        return f"{head} Demand, staffing and consultation length are in line with other weekdays.{trend}"
    lead = "Two things drive it" if len(reasons) > 1 else "What drives it"
    return f"{head} {lead}: {'; and '.join(reasons)}.{trend}"
