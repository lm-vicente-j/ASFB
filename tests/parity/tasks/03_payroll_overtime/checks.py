from payroll import gross_weekly_pay


def test_overtime_only_applies_above_forty_hours():
    assert gross_weekly_pay(20, 45) == 950.0


def test_exactly_forty_hours_has_no_overtime():
    assert gross_weekly_pay(20, 40) == 800.0


def test_under_threshold_is_plain_rate():
    assert gross_weekly_pay(20, 30) == 600.0


def test_mixed_hours_rounded_to_cents():
    assert gross_weekly_pay(10.01, 41.5) == 422.92


def test_sixty_hours():
    assert gross_weekly_pay(10, 60) == 700.0


def test_zero_hours():
    assert gross_weekly_pay(20, 0) == 0.0


def test_negative_hours_raises():
    try:
        gross_weekly_pay(20, -1)
    except ValueError:
        return
    assert False, "expected ValueError for negative hours"
