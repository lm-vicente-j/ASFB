from billing import apply_discount


def test_discount_is_capped_at_fifty_percent():
    assert apply_discount(100, 80) == 50.0


def test_cap_boundary_is_unchanged():
    assert apply_discount(200, 50) == 100.0


def test_normal_discount():
    assert apply_discount(100, 15) == 85.0


def test_result_rounded_to_cents():
    assert apply_discount(33.33, 20) == 26.66


def test_zero_discount_returns_total():
    assert apply_discount(49.5, 0) == 49.5


def test_negative_pct_raises():
    try:
        apply_discount(100, -5)
    except ValueError:
        return
    assert False, "expected ValueError for negative pct"


def test_negative_total_raises():
    try:
        apply_discount(-1, 10)
    except ValueError:
        return
    assert False, "expected ValueError for negative total"
