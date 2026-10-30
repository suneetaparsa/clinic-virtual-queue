"""End to end: join on the web, confirm by text, check in at the desk, see the visit counted."""
from app import queue_service as qs
from app.db import session_scope
from app.main import run_tick

from .conftest import PHONE, join_web, last_text


def staff(client):
    client.post("/staff/login", data={"pin": "2468"})
    return client


def entry(token):
    with session_scope() as s:
        return qs.entry_by_token(s, token)


def run_until(predicate, max_minutes=240):
    for _ in range(max_minutes):
        if predicate():
            return True
        qs.runtime.clock.advance(1)
        run_tick()
    return predicate()


def test_full_visit(at_start):
    c = at_start
    place = join_web(c)
    token = place.split("/q/")[1]
    assert "You're in line at Sunrise Community Clinic" in last_text()
    page = c.get(place)
    assert page.status_code == 200 and "Your line ID" in page.text and "<svg" in page.text
    assert page.headers["cache-control"] == "no-store"

    # Confirmation text arrives when the estimate reaches 25 minutes.
    assert run_until(lambda: entry(token).status == "awaiting")
    assert "Reply Y within 20 min" in last_text()
    c.post("/demo/phone/number", data={"number": PHONE})
    c.post("/demo/phone/send", data={"body": "y"})
    assert entry(token).status == "confirmed"

    # Front desk checks the patient in by line ID, then calls them to a room.
    staff(c)
    line_id = entry(token).line_id
    r = c.post("/staff/lookup", data={"query": line_id.replace("-", "").lower()}, follow_redirects=True)
    assert "Mark arrived" in r.text
    c.post(f"/staff/entry/{line_id}/arrive")
    assert entry(token).status == "arrived"
    assert run_until(lambda: "A room is free" in c.get("/staff").text, 120)
    c.post(f"/staff/entry/{line_id}/room")
    assert entry(token).status == "in_room"
    c.post(f"/staff/entry/{line_id}/complete")
    assert entry(token).status == "completed"
    assert "Visit complete" in c.get(place).text


def test_no_reply_removes_after_20_minutes_with_reminder(at_start):
    c = at_start
    token = join_web(c).split("/q/")[1]
    assert run_until(lambda: entry(token).status == "awaiting")
    sent = entry(token).confirm_sent_at
    assert run_until(lambda: entry(token).status == "removed", 25)
    removed_after = (qs.now() - sent).total_seconds() / 60
    assert 19 <= removed_after <= 21
    texts = [m.body for m in __import__("app.messaging", fromlist=["x"]).demo_gateway.thread(PHONE) if m.direction == "in"]
    assert any("10 min left" in t for t in texts)
    assert "Rejoin" in texts[-1]


def test_staff_remove_needs_two_taps(at_start):
    c = staff(at_start)
    token = join_web(c).split("/q/")[1]
    lid = entry(token).line_id
    c.post(f"/staff/entry/{lid}/remove")
    assert entry(token).status == "waiting"
    c.post(f"/staff/entry/{lid}/remove", data={"confirm": "yes"})
    assert entry(token).status == "removed"


def test_leave_from_page(at_start):
    c = at_start
    place = join_web(c)
    assert "Leave the line?" in c.get(place + "?confirm_leave=1").text
    c.post(place + "/leave")
    assert entry(place.split("/q/")[1]).status == "left"
    assert "Join again" in c.get(place).text


def test_dashboard_counts_and_weekly_pattern(at_start):
    c = staff(at_start)
    for _ in range(60):
        qs.runtime.clock.advance(1)
        run_tick()
    page = c.get("/staff").text
    assert "Arrived at desk" in page and "Consultation started" in page and "Consultation ended" in page
    assert "Tuesday 2 PM" in page  # worst cell selected by default
    assert "capacity is lower" in page
