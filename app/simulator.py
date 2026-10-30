"""Demo simulator: a synthetic clinic morning and 8 weeks of synthetic history.

Stands in for real patients and for the health record feed (visit start and
end) so the service can be demonstrated end to end. Never runs in production
(DEMO_MODE=false).
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from . import queue_service as qs
from .db import Base, engine
from .models import (
    ARRIVED,
    AWAITING,
    CONFIRMED,
    IN_ROOM,
    LEFT,
    REMOVED,
    WAITING,
    Clinic,
    HourlyStat,
    QueueEntry,
    VisitDuration,
)
from .text_commands import reset_pending

NAMES = [
    "Luis", "Aaliyah", "Tom", "Mei", "Darnell", "Rosa", "Ivan", "Keisha", "Omar", "Linda", "Jamal", "Sofia",
    "Brandon", "Ana", "Kevin", "Fatima", "Jorge", "Brittany", "Hung", "Destiny", "Carlos", "Megan", "Andre",
    "Lupe", "Nadia", "Ray", "Gabriela", "Tyler", "Ming", "Esperanza", "Marcus", "Yesenia", "Dale", "Priya",
]
THIRD_PROVIDER_AT = time(9, 30)


class Simulator:
    def __init__(self, seed: int = 20261029) -> None:
        self.rng = random.Random(seed)
        self.next_arrival: datetime | None = None

    # ------------------------------------------------------------ helpers
    def _visit_minutes(self) -> float:
        return min(45.0, max(4.0, 14.0 * math.exp(0.5 * self.rng.gauss(0, 1))))

    def _phone(self) -> str:
        return "555010" + f"{self.rng.randint(1000, 9999)}"

    def _plan_reply(self, e: QueueEntry, sent_at: datetime) -> None:
        r = self.rng.random()
        if r < 0.88:
            e.sim_reply, e.sim_reply_at = "yes", sent_at + timedelta(minutes=self.rng.uniform(1, 12))
        elif r < 0.95:
            e.sim_reply, e.sim_reply_at = "no", sent_at + timedelta(minutes=self.rng.uniform(1, 8))
        else:
            e.sim_reply = "none"

    def _synthetic(self, s: Session, clinic: Clinic, at: datetime, status: str = WAITING) -> QueueEntry:
        e, _ = qs.create_entry(
            s, clinic, self.rng.choice(NAMES), self.rng.randint(1950, 2020), self._phone(), "en", "web", at, synthetic=True
        )
        e.status = status
        return e

    # ------------------------------------------------------------ seeding
    def reset(self, s: Session, at: datetime, empty: bool = False) -> Clinic:
        self.rng.seed(20261029)
        reset_pending()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        clinic = Clinic(
            slug="sunrise", name="Sunrise Community Clinic", name_es="Clínica Comunitaria Sunrise",
            phone_display="(555) 010-2200", opens=time(8, 0), closes=time(17, 0), providers_working=2,
            line_opens=time(7, 30), last_join=time(16, 30), open_weekdays="01234",
            accepting=True, hourly_cap_per_provider=12,
        )
        s.add(clinic)
        s.flush()
        if not empty:
            self._seed_today(s, clinic, at)
        self._seed_history(s, clinic, at)
        start = max(at, datetime.combine(at.date(), clinic.line_opens))
        self.next_arrival = start + timedelta(minutes=self.rng.expovariate(1 / 6))
        return clinic

    def _seed_today(self, s: Session, clinic: Clinic, at: datetime) -> None:
        day = at.date()
        d = lambda h, m: datetime.combine(day, time(h, m))  # noqa: E731
        for minutes in (5, 11, 13):
            s.add(VisitDuration(clinic_id=clinic.id, day=day, weekday=day.weekday(), hour=8, providers=2, minutes=minutes))
        # Two finished visits, two in rooms, one confirmed, two awaiting reply, six waiting.
        for joined, arrived, room, done in ((d(7, 20), d(7, 56), d(8, 0), d(8, 5)), (d(7, 25), d(7, 58), d(8, 0), d(8, 11))):
            e = self._synthetic(s, clinic, joined, "completed")
            e.arrived_at, e.room_at, e.done_at, e.est_call_at_join = arrived, room, done, None
        for joined, arrived, room in ((d(7, 35), d(8, 2), d(8, 5)), (d(7, 41), d(8, 6), d(8, 11))):
            e = self._synthetic(s, clinic, joined, IN_ROOM)
            e.arrived_at, e.room_at, e.sim_visit_minutes = arrived, room, self._visit_minutes()
            e.est_call_at_join = None
        e = self._synthetic(s, clinic, d(7, 47), CONFIRMED)
        e.confirm_sent_at, e.confirmed_at, e.sim_arrive_at = d(8, 1), d(8, 3), at + timedelta(minutes=6)
        for i, sent in enumerate((d(8, 5), d(8, 12))):
            e = self._synthetic(s, clinic, d(7, 52) + timedelta(minutes=4 * i), AWAITING)
            e.confirm_sent_at = sent
            self._plan_reply(e, sent)
        for j in (d(7, 58), d(8, 2), d(8, 5), d(8, 8), d(8, 11), d(8, 14)):
            e = self._synthetic(s, clinic, j)
            if self.rng.random() < 0.12:
                e.sim_leave_at = at + timedelta(minutes=self.rng.uniform(5, 35))
        s.flush()
        for e in qs.lineup(s, clinic, day):
            e.est_call_at_join = qs.est_call_time(s, clinic, e, at)

    def _seed_history(self, s: Session, clinic: Clinic, at: datetime) -> None:
        """8 weeks, Monday to Friday, 8 AM to 4 PM. Tuesday 2 PM has 50% more
        demand and, from the 4th week, one provider fewer (a staff meeting)."""
        r = random.Random(8812)
        base_arr = {8: 8, 9: 9, 10: 8, 11: 7, 12: 6, 13: 7, 14: 7.5, 15: 7, 16: 5}
        base_prov = {8: 2.5, 9: 3, 10: 3, 11: 3, 12: 2.5, 13: 3, 14: 3, 15: 3, 16: 3}
        monday = at.date() - timedelta(days=at.weekday())
        for w in range(8):
            week = monday - timedelta(weeks=8 - w)
            for d in range(5):
                day = week + timedelta(days=d)
                backlog = 0.0
                for h in range(8, 17):
                    mult = 1.15 if d == 0 and h <= 9 else 0.8 if d == 4 and h >= 14 else 1.5 if d == 1 and h == 14 else 1.0
                    prov = 2.0 if d == 1 and h == 14 and w >= 3 else base_prov[h]
                    m = base_arr[h] * mult
                    arrivals = max(0, round(m + (r.random() + r.random() + r.random() - 1.5) * math.sqrt(m) * 0.8))
                    consult = max(10.0, 15 + (r.random() - 0.5) * 3)
                    cap = prov * 60 / consult
                    end = max(0.0, backlog + arrivals - cap)
                    wait = 5 + (backlog + end) / 2 / cap * 60
                    s.add(HourlyStat(clinic_id=clinic.id, day=day, weekday=d, hour=h, arrivals=arrivals, providers=prov, consult_minutes=consult, avg_wait_minutes=wait))
                    backlog = end

    # ------------------------------------------------------------ live tick
    def tick(self, s: Session, clinic: Clinic, at: datetime) -> None:
        day = at.date()
        if at.time() >= THIRD_PROVIDER_AT and clinic.providers_working < 3:
            clinic.providers_working = 3
        # New synthetic check-ins (about one every 6 minutes).
        while self.next_arrival and at >= self.next_arrival:
            if clinic.accepting and qs.can_join(clinic, self.next_arrival):
                e = self._synthetic(s, clinic, self.next_arrival)
                s.flush()
                e.est_call_at_join = qs.est_call_time(s, clinic, e, self.next_arrival)
                if self.rng.random() < 0.12:
                    e.sim_leave_at = self.next_arrival + timedelta(minutes=self.rng.uniform(5, 40))
            self.next_arrival += timedelta(minutes=self.rng.expovariate(1 / 6))
        # Record feed: visits end after their simulated length (demo patient included).
        for e in qs.in_rooms(s, clinic, day):
            if e.sim_visit_minutes is None:
                e.sim_visit_minutes = self._visit_minutes()
            if (at - e.room_at).total_seconds() / 60 >= e.sim_visit_minutes:
                qs.complete(s, clinic, e, e.room_at + timedelta(minutes=e.sim_visit_minutes), actor="record_feed")
        # Synthetic patient behaviour.
        for e in qs.lineup(s, clinic, day):
            if not e.synthetic:
                continue
            if e.status == WAITING and e.sim_leave_at and at >= e.sim_leave_at:
                qs.end(s, e, LEFT, "walked_away", "patient", at)
            elif e.status == AWAITING:
                if e.sim_reply is None and e.confirm_sent_at:
                    self._plan_reply(e, e.confirm_sent_at)
                if e.sim_reply == "yes" and at >= e.sim_reply_at:
                    qs.confirm(s, e, e.sim_reply_at)
                    e.sim_arrive_at = e.sim_reply_at + timedelta(minutes=self.rng.uniform(4, 15))
                elif e.sim_reply == "no" and at >= e.sim_reply_at:
                    qs.end(s, e, LEFT, "declined", "patient", at)
                elif e.confirm_sent_at and (at - e.confirm_sent_at).total_seconds() / 60 >= 20:
                    qs.end(s, e, REMOVED, "no_reply", "system", at)
            elif e.status == CONFIRMED and e.sim_arrive_at and at >= e.sim_arrive_at:
                qs.arrive(s, e, e.sim_arrive_at, actor="simulator")
        # Synthetic patients at the desk go into free rooms; the demo patient waits for staff.
        free = 0 if at.time() < clinic.opens else clinic.providers_working - len(qs.in_rooms(s, clinic, day))
        for e in qs.lineup(s, clinic, day):
            if free <= 0 or e.status != ARRIVED:
                continue
            if not e.synthetic:
                break
            qs.call_to_room(s, e, at, actor="simulator")
            free -= 1


simulator = Simulator()
