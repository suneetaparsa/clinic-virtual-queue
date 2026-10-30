from datetime import timedelta

from app import queue_service as qs
from app.db import session_scope
from app.models import QueueEntry
from app.routes_demo import reset_demo

from .conftest import last_text


def send(c, body, number="5550104477"):
    c.post("/demo/phone/number", data={"number": number})
    c.post("/demo/phone/send", data={"body": body})
    return last_text(number)


def test_text_join_wait_leave(at_start):
    c = at_start
    assert "waiting, estimated wait" in send(c, "WAIT")
    assert "first name and birth year" in send(c, "JOIN")
    assert "4-digit birth year" in send(c, "Maria")
    assert "You're in line at" in send(c, "Maria 1988")
    assert "You're in line. ID" in send(c, "wait")
    assert "already in line" in send(c, "JOIN")
    assert "You've left the line" in send(c, "leave")


def test_spanish_commands_reply_in_spanish(at_start):
    c = at_start
    assert "esperando" in send(c, "ESPERA")
    assert "primer nombre" in send(c, "UNIRSE")
    assert "Está en la fila" in send(c, "María 1990")


def test_stop_silences_and_start_resumes(at_start):
    c = at_start
    assert "won't get more texts" in send(c, "STOP")
    before = last_text()
    send(c, "WAIT")
    assert last_text() == before  # no reply while opted out
    assert "Texts are back on" in send(c, "START")


def test_before_opening_join_is_refused_and_wait_includes_doctor_start(client):
    reset_demo("early")  # 7:00 AM
    qs.runtime.clock.set_running(False)
    c = client
    home = c.get("/c/sunrise").text
    assert "the line opens at 7:30 AM" in home and "Join the line</a>" not in home
    assert "isn't taking check-ins" in send(c, "JOIN")
    qs.runtime.clock.advance(35)  # 7:35 AM: the line is open, doctors start at 8:00
    home = c.get("/c/sunrise").text
    assert "doctors start at 8:00 AM" in home and "Join the line</a>" in home
    with session_scope() as s:
        clinic = qs.get_clinic(s)
        assert qs.estimate_for(s, clinic, None, qs.now()).mid >= 24


def test_after_closing(client):
    reset_demo("after")  # 5:05 PM Friday
    qs.runtime.clock.set_running(False)
    home = client.get("/c/sunrise").text
    assert "Closed for today · opens Monday at 8:00 AM" in home
    assert "Monday" in send(client, "JOIN")


def test_purge_removes_personal_fields_after_48_hours(at_start):
    with session_scope() as s:
        n = qs.purge_personal_data(s, qs.now() + timedelta(days=3))
        assert n > 0
        e = s.query(QueueEntry).first()
        assert e.first_name is None and e.phone_cipher is None and e.purged
