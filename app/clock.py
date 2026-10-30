"""Clinic clock.

Production uses the real local time. Demo mode runs a simulated clock that
starts at 08:15 on the demo day and can be sped up (1x, 10x, 30x, 60x) so a
morning plays out in minutes. All times are naive datetimes in clinic local time.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta


class Clock:
    def now(self) -> datetime:  # pragma: no cover - trivial
        return datetime.now()

    @property
    def simulated(self) -> bool:
        return False


class SimulatedClock(Clock):
    def __init__(self, start: datetime, speed: float = 10.0) -> None:
        self._lock = threading.Lock()
        self._start = start
        self.reset(start, speed)

    def reset(self, start: datetime | None = None, speed: float | None = None) -> None:
        with self._lock:
            self._base_sim = start or self._start
            self._base_real = time.monotonic()
            self._speed = speed if speed is not None else getattr(self, "_speed", 10.0)
            self._running = True

    def now(self) -> datetime:
        with self._lock:
            if not self._running:
                return self._base_sim
            elapsed = time.monotonic() - self._base_real
            return self._base_sim + timedelta(seconds=elapsed * self._speed)

    def _rebase(self) -> None:
        now = self.now()
        with self._lock:
            self._base_sim = now
            self._base_real = time.monotonic()

    def set_speed(self, speed: float) -> None:
        self._rebase()
        with self._lock:
            self._speed = speed

    def set_running(self, running: bool) -> None:
        self._rebase()
        with self._lock:
            self._running = running

    def advance(self, minutes: float) -> None:
        """Jump forward (used by tests and the demo's skip button)."""
        self._rebase()
        with self._lock:
            self._base_sim += timedelta(minutes=minutes)

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def running(self) -> bool:
        return self._running

    @property
    def simulated(self) -> bool:
        return True


DEMO_START = datetime(2026, 10, 30, 8, 15)
