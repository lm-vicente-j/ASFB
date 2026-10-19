from shipping import shipping_cost


def test_domestic_rate_unchanged():
    assert shipping_cost(2, "domestic") == 7.4


def test_regional_rate_unchanged():
    assert shipping_cost(3, "regional") == 15.8


def test_international_rate_unchanged():
    assert shipping_cost(2, "international") == 27.5


def test_result_rounded_to_cents():
    assert shipping_cost(1.333, "domestic") == 6.6


def test_remote_zone_rate():
    assert shipping_cost(2, "remote") == 18.8


def test_remote_zone_small_parcel():
    assert shipping_cost(0.5, "remote") == 13.7


def test_unknown_zone_raises():
    try:
        shipping_cost(1, "moon")
    except ValueError:
        return
    assert False, "expected ValueError for unknown zone"


def test_non_positive_weight_raises():
    try:
        shipping_cost(0, "domestic")
    except ValueError:
        return
    assert False, "expected ValueError for zero weight"
