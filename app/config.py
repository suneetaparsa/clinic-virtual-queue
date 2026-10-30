"""Runtime settings, read once from environment variables.

Every setting has a safe default for local demo use. Production deployments
must set SECRET_KEY, PHONE_ENCRYPTION_KEY and STAFF_PIN explicitly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./clinic_queue.db"))
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", "dev-only-change-me-0123456789abcdef"))
    # Fernet key (32 url-safe base64 bytes). Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    phone_encryption_key: str = field(default_factory=lambda: os.getenv("PHONE_ENCRYPTION_KEY", ""))
    staff_pin: str = field(default_factory=lambda: os.getenv("STAFF_PIN", "2468"))

    # Demo mode: simulated clock, synthetic patients, on-screen "demo phone" for texts.
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", True))
    demo_speed: float = field(default_factory=lambda: float(os.getenv("DEMO_SPEED", "10")))

    # Join protection: text a one-time code before a web join completes.
    join_one_time_code: bool = field(default_factory=lambda: _bool("JOIN_ONE_TIME_CODE", True))

    # Queue rules (minutes) from the Phase 1 technical specification.
    confirm_at_minutes: int = 25
    reply_window_minutes: int = 20
    reminder_after_minutes: int = 10
    accuracy_band_minutes: int = 20
    retention_hours_after_visit_day: int = 48

    # Abuse limits.
    join_attempts_per_10_min: int = field(default_factory=lambda: int(os.getenv("JOIN_ATTEMPTS_PER_10_MIN", "6")))
    code_tries: int = 3

    # Twilio (leave blank to use the on-screen demo phone).
    twilio_account_sid: str = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    twilio_from_number: str = field(default_factory=lambda: os.getenv("TWILIO_FROM_NUMBER", ""))
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", ""))

    @property
    def twilio_enabled(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number)


settings = Settings()
