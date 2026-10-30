"""Tokens, phone protection, staff sessions and rate limits.

Design rules (see Phase 1 specification, "Privacy and access"):
- A patient's page is opened by a random 192-bit token, never by the short line ID.
- Only a SHA-256 hash of the token is stored, so a database leak cannot open pages.
- Phone numbers are stored encrypted, plus a keyed hash for "one entry per phone" checks.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet

from .config import settings

_PHONE_RE = re.compile(r"\D")
LINE_ID_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ"  # no I, L, O
LINE_ID_DIGITS = "23456789"  # no 0, 1


def normalize_us_phone(raw: str) -> str | None:
    digits = _PHONE_RE.sub("", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def format_phone(digits: str) -> str:
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def mask_phone(digits: str) -> str:
    return f"(•••) •••-{digits[6:]}"


def phone_hash(digits: str) -> str:
    return hmac.new(settings.secret_key.encode(), b"phone:" + digits.encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    key = settings.phone_encryption_key
    if not key:
        # Derive a stable development key from SECRET_KEY. Production must set PHONE_ENCRYPTION_KEY.
        key = base64.urlsafe_b64encode(hashlib.sha256(b"fernet:" + settings.secret_key.encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt_phone(digits: str) -> str:
    return _fernet().encrypt(digits.encode()).decode()


def decrypt_phone(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode()).decode()


def new_token() -> str:
    return secrets.token_urlsafe(24)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_line_id() -> str:
    return (
        secrets.choice(LINE_ID_LETTERS)
        + secrets.choice(LINE_ID_DIGITS)
        + "-"
        + str(10 + secrets.randbelow(90))
    )


def normalize_line_id(raw: str) -> str | None:
    s = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if len(s) != 4:
        return None
    return f"{s[:2]}-{s[2:]}"


def new_code() -> str:
    return f"{secrets.randbelow(10000):04d}"


def code_hash(code: str, salt: str) -> str:
    return hmac.new(settings.secret_key.encode(), f"code:{salt}:{code}".encode(), hashlib.sha256).hexdigest()


# ---- staff session cookie -------------------------------------------------
STAFF_COOKIE = "staff_session"
STAFF_IDLE_SECONDS = 15 * 60


def make_staff_cookie() -> str:
    expires = int(time.time()) + STAFF_IDLE_SECONDS
    payload = f"staff:{expires}"
    sig = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def staff_cookie_valid(value: str | None) -> bool:
    if not value:
        return False
    try:
        role, expires, sig = value.split(":")
    except ValueError:
        return False
    payload = f"{role}:{expires}"
    good = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return role == "staff" and hmac.compare_digest(good, sig) and int(expires) > time.time()


def pin_ok(pin: str) -> bool:
    return hmac.compare_digest((pin or "").strip(), settings.staff_pin)


# ---- rate limiting (in memory; single instance) ---------------------------
class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()
