from bookings import overlaps


def test_partial_overlap():
    assert overlaps((540, 600), (570, 630)) is True


def test_contained_interval_overlaps():
    assert overlaps((540, 720), (600, 630)) is True


def test_touching_end_to_start_does_not_overlap():
    assert overlaps((540, 600), (600, 660)) is False


def test_touching_start_to_end_does_not_overlap():
    assert overlaps((600, 660), (540, 600)) is False


def test_disjoint_does_not_overlap():
    assert overlaps((540, 600), (700, 800)) is False


def test_identical_bookings_overlap():
    assert overlaps((540, 600), (540, 600)) is True


def test_overlap_is_symmetric():
    assert overlaps((570, 630), (540, 600)) == overlaps((540, 600), (570, 630))
