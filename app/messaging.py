"""Outbound text-message gateways.

DemoGateway keeps messages in memory for the on-screen demo phone (never in
the database). TwilioGateway sends real texts once the account, a Business
Associate Agreement and carrier registration are in place.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

from .config import settings

log = logging.getLogger("clinic.messaging")


@dataclass
class DemoMessage:
    direction: str  # "in" = clinic -> phone, "out" = phone -> clinic (as seen on the phone)
    body: str
    at: datetime


class Gateway:
    def send(self, to_digits: str, body: str, at: datetime) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class DemoGateway(Gateway):
    def __init__(self, max_per_phone: int = 200) -> None:
        self._inbox: dict[str, deque[DemoMessage]] = defaultdict(lambda: deque(maxlen=max_per_phone))
        self._lock = threading.Lock()

    def send(self, to_digits: str, body: str, at: datetime) -> None:
        with self._lock:
            self._inbox[to_digits].append(DemoMessage("in", body, at))

    def record_inbound(self, from_digits: str, body: str, at: datetime) -> None:
        with self._lock:
            self._inbox[from_digits].append(DemoMessage("out", body, at))

    def thread(self, digits: str) -> list[DemoMessage]:
        with self._lock:
            return list(self._inbox.get(digits, []))

    def clear(self) -> None:
        with self._lock:
            self._inbox.clear()


class TwilioGateway(Gateway):
    """Minimal Twilio Messaging client using the standard library."""

    def send(self, to_digits: str, body: str, at: datetime) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
        data = urllib.parse.urlencode({"To": "+1" + to_digits, "From": settings.twilio_from_number, "Body": body}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        token = base64.b64encode(f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed https host
                resp.read()
        except Exception:  # pragma: no cover - network
            log.exception("Twilio send failed")


def twilio_signature_valid(url: str, params: dict[str, str], signature: str) -> bool:
    """Twilio request validation: HMAC-SHA1 of the URL plus sorted parameters."""
    payload = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(settings.twilio_auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


demo_gateway = DemoGateway()
gateway: Gateway = TwilioGateway() if settings.twilio_enabled else demo_gateway
