"""Nobody can see another patient's place, wait or texts."""
import re

from app import queue_service as qs
from app.config import settings
from app.db import session_scope
from app.messaging import demo_gateway
from fastapi.testclient import TestClient

from .conftest import PHONE, join_web, last_text


def _strip_ids(html: str) -> str:
    return re.sub(r"/verify/[A-Za-z0-9_\-]+", "/verify/X", html)


def test_join_form_does_not_reveal_who_is_in_line(at_start):
    c = at_start
    join_web(c)  # the real patient is now in line
    stranger = TestClient(c.app)
    other = "5550109999"
    r_in = stranger.post("/c/sunrise/join", data={"name": "Sam", "year": "1990", "phone": PHONE, "consent": "1"}, follow_redirects=True)
    r_new = stranger.post("/c/sunrise/join", data={"name": "Sam", "year": "1990", "phone": other, "consent": "1"}, follow_redirects=True)
    assert r_in.status_code == r_new.status_code == 200
    assert "already in line" not in r_in.text
    # Identical pages apart from the masked last 4 digits and the random form address.
    assert _strip_ids(r_in.text).replace("•••-4477", "•••-NNNN") == _strip_ids(r_new.text).replace("•••-9999", "•••-NNNN")
    # The code went to the real owner's phone, never to the stranger.
    assert "code:" in last_text(PHONE)


def test_wrong_codes_lock_after_three_tries(at_start):
    c = at_start
    r = c.post("/c/sunrise/join", data={"name": "Sam", "year": "1990", "phone": PHONE, "consent": "1"}, follow_redirects=False)
    url = r.headers["location"]
    for _ in range(3):
        r = c.post(url, data={"code": "0000" if "code: 0000" not in last_text() else "1111"})
    assert "Too many tries" in r.text


def test_place_page_needs_the_secret_token_not_the_line_id(at_start):
    c = at_start
    place = join_web(c)
    with session_scope() as s:
        e = qs.entry_by_token(s, place.split("/q/")[1])
        lid = e.line_id
        assert e.token_hash != place.split("/q/")[1]  # only a hash is stored
    assert c.get(f"/q/{lid}").status_code == 404
    assert c.get("/q/" + "x" * 32).status_code == 404


def test_phone_is_encrypted_at_rest(at_start):
    c = at_start
    place = join_web(c)
    with session_scope() as s:
        e = qs.entry_by_token(s, place.split("/q/")[1])
        assert PHONE not in (e.phone_cipher or "") and PHONE not in (e.phone_hash or "")


def test_text_commands_only_touch_the_senders_entry(at_start):
    c = at_start
    place = join_web(c)
    c.post("/demo/phone/number", data={"number": "5550108888"})
    c.post("/demo/phone/send", data={"body": "LEAVE"})
    with session_scope() as s:
        assert qs.entry_by_token(s, place.split("/q/")[1]).status == "waiting"
    assert "You're not in line" in last_text("5550108888")


def test_staff_console_requires_sign_in(at_start):
    fresh = TestClient(at_start.app)
    r = fresh.get("/staff", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/staff/login"
    r = fresh.post("/staff/login", data={"pin": "0000"}, follow_redirects=True)
    assert "That PIN is not right" in r.text


def test_texts_never_include_name_or_birth_year(at_start):
    c = at_start
    join_web(c, name="Zelda", year="2018")
    assert all("Zelda" not in m.body and "2018" not in m.body for m in demo_gateway.thread(PHONE))


def test_one_time_code_is_on_by_default():
    assert settings.join_one_time_code is True
