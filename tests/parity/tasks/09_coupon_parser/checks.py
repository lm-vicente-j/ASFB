from promo import Coupon, parse_coupon


def test_basic_code_parses():
    assert parse_coupon("SAVE10-2026") == Coupon(percent=10, year=2026)


def test_prefix_is_case_insensitive():
    coupon = parse_coupon("save25-2027")
    assert coupon.percent == 25
    assert coupon.year == 2027


def test_percent_bounds_are_inclusive():
    assert parse_coupon("SAVE1-2026").percent == 1
    assert parse_coupon("SAVE90-2026").percent == 90


def test_percent_zero_raises():
    try:
        parse_coupon("SAVE0-2026")
    except ValueError:
        return
    assert False, "expected ValueError for percent 0"


def test_percent_above_ninety_raises():
    try:
        parse_coupon("SAVE91-2026")
    except ValueError:
        return
    assert False, "expected ValueError for percent 91"


def test_wrong_prefix_raises():
    try:
        parse_coupon("PROMO10-2026")
    except ValueError:
        return
    assert False, "expected ValueError for wrong prefix"


def test_malformed_codes_raise():
    for code in ["SAVE10", "SAVE-5-2026", "SAVEXX-2026", "SAVE10-26"]:
        try:
            parse_coupon(code)
        except ValueError:
            continue
        assert False, "expected ValueError for " + code
