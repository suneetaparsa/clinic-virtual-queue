import os
import re
import tempfile

# Settings are read at import time, so point at a throwaway database first.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["DEMO_MODE"] = "true"
os.environ["JOIN_ONE_TIME_CODE"] = "true"
os.environ.setdefault("STAFF_PIN", "2468")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import queue_service as qs  # noqa: E402
from app.main import create_app  # noqa: E402
from app.messaging import demo_gateway  # noqa: E402
from app.routes_demo import reset_demo  # noqa: E402

PHONE = "5550104477"


@pytest.fixture()
def client():
    app = create_app(run_scheduler=False)
    with TestClient(app) as c:
        qs.runtime.clock.set_running(False)  # tests move the clock explicitly
        yield c


@pytest.fixture()
def at_start(client):
    """Fresh 8:15 AM demo day with the clock stopped."""
    reset_demo("morning")
    qs.runtime.clock.set_running(False)
    return client


def last_text(digits: str = PHONE) -> str:
    msgs = [m for m in demo_gateway.thread(digits) if m.direction == "in"]
    return msgs[-1].body if msgs else ""


def code_from_text(digits: str = PHONE) -> str:
    return re.search(r"code: (\d{4})", last_text(digits)).group(1)


def join_web(client, name="Ava", year="2019", phone=PHONE):
    r = client.post("/c/sunrise/join", data={"name": name, "year": year, "phone": phone, "consent": "1"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    verify_url = r.headers["location"]
    r = client.post(verify_url, data={"code": code_from_text(phone)}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]  # /q/<token>
