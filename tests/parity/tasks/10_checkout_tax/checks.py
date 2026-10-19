from checkout import checkout_total


def test_california_tax_applied():
    assert checkout_total([(10, 2)], "United States", "US-CA") == 21.45


def test_new_york_tax_applied():
    assert checkout_total([(100, 2)], "United States", "US-NY") == 217.75


def test_texas_multiple_line_items():
    assert checkout_total([(10.00, 1), (2.50, 4)], "United States", "US-TX") == 21.25


def test_german_region_rate():
    assert checkout_total([(50, 1)], "Germany", "DE-BE") == 59.5


def test_canadian_region_rate():
    assert checkout_total([(10, 2)], "Canada", "CA-ON") == 22.6


def test_unknown_region_charges_no_tax():
    assert checkout_total([(5, 3)], "France", "FR-XX") == 15.0
