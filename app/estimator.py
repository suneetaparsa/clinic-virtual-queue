"""Wait-time estimator (Phase 1 rule-based model).

    W_k = (R + sum_{j<k} p_j * d) / P

R  = minutes left for patients already in rooms
p_j = probability that patient j ahead is seen
d  = expected visit length (median of today's last 10 visits, else 15)
P  = providers working

Range = W_k +/- max(10, 1.28 * sigma * sqrt(n) / P), rounded to 5 minutes,
targeting 80% of real call times inside the range. Pure functions only, so
the model is easy to test and replace.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

DEFAULT_VISIT_MINUTES = 15.0
DEFAULT_SIGMA = 8.0
SHOW_PROBABILITY = {"confirmed": 0.97, "arrived": 0.97, "awaiting": 0.88, "waiting": 0.85}


@dataclass(frozen=True)
class Estimate:
    mid: float  # minutes
    lo: int
    hi: int
    ahead: int

    @property
    def under_ten(self) -> bool:
        return self.hi <= 10

    def text(self, lang: str = "en") -> str:
        if self.under_ten:
            return "menos de 10 min" if lang == "es" else "under 10 min"
        return f"{self.lo}–{self.hi} min"


def _round5(x: float) -> int:
    return max(0, int(round(x / 5.0)) * 5)


def visit_stats(recent_minutes: list[float]) -> tuple[float, float]:
    last = recent_minutes[-10:]
    d = statistics.median(last) if len(last) >= 3 else DEFAULT_VISIT_MINUTES
    sigma = statistics.stdev(recent_minutes) if len(recent_minutes) >= 8 else DEFAULT_SIGMA
    return d, sigma


def estimate(
    ahead_statuses: list[str],
    in_room_elapsed_minutes: list[float],
    recent_visit_minutes: list[float],
    providers: int,
    start_delay_minutes: float = 0.0,
) -> Estimate:
    providers = max(1, providers)
    d, sigma = visit_stats(recent_visit_minutes)
    remaining_in_rooms = sum(max(d - elapsed, 2.0) for elapsed in in_room_elapsed_minutes)
    expected_ahead = sum(SHOW_PROBABILITY.get(s, 0.85) * d for s in ahead_statuses)
    n = len(ahead_statuses)
    free_rooms = max(0, providers - len(in_room_elapsed_minutes))
    mid = 0.0 if n < free_rooms else (remaining_in_rooms + expected_ahead) / providers
    mid += max(0.0, start_delay_minutes)  # before opening: add the time until doctors start
    half = max(10.0, 1.28 * sigma * math.sqrt(max(n, 1)) / providers)
    lo = _round5(mid - half)
    hi = max(10, _round5(mid + half))
    return Estimate(mid=mid, lo=lo, hi=hi, ahead=n)
