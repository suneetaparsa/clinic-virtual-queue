from app.estimator import estimate


def test_empty_line_with_free_room_is_under_ten():
    e = estimate([], [], [], providers=2)
    assert e.under_ten and e.text() == "under 10 min" and e.text("es") == "menos de 10 min"


def test_wait_grows_with_people_ahead_and_shrinks_with_providers():
    five = estimate(["waiting"] * 5, [3, 3], [15, 15, 15], providers=2)
    ten = estimate(["waiting"] * 10, [3, 3], [15, 15, 15], providers=2)
    ten_three = estimate(["waiting"] * 10, [3, 3], [15, 15, 15], providers=3)
    assert five.mid < ten.mid
    assert ten_three.mid < ten.mid


def test_range_is_at_least_plus_minus_ten_and_rounded_to_five():
    e = estimate(["confirmed"] * 3, [5, 5], [12, 14, 16], providers=2)
    assert e.lo % 5 == 0 and e.hi % 5 == 0
    assert e.hi - e.lo >= 20


def test_confirmed_patients_count_more_than_unconfirmed():
    confirmed = estimate(["confirmed"] * 6, [], [15, 15, 15], providers=1)
    waiting = estimate(["waiting"] * 6, [], [15, 15, 15], providers=1)
    assert confirmed.mid > waiting.mid


def test_before_opening_adds_time_until_doctors_start():
    e = estimate([], [], [], providers=2, start_delay_minutes=25)
    assert 20 <= e.mid <= 30
