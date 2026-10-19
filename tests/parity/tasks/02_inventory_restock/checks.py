from inventory import items_to_restock


def test_strictly_below_threshold_only():
    stock = {"A-100": 4, "B-200": 5, "C-300": 3}
    assert items_to_restock(stock, 5) == ["A-100", "C-300"]


def test_result_sorted_ascending():
    stock = {"Z-9": 1, "A-1": 2, "M-5": 0}
    assert items_to_restock(stock, 3) == ["A-1", "M-5", "Z-9"]


def test_empty_stock_returns_empty_list():
    assert items_to_restock({}, 10) == []


def test_nothing_below_threshold_returns_empty_list():
    assert items_to_restock({"X-1": 1, "X-2": 7}, 1) == []


def test_zero_quantity_is_included():
    assert items_to_restock({"Q-1": 0}, 1) == ["Q-1"]


def test_returns_a_list():
    assert isinstance(items_to_restock({"K-1": 2}, 5), list)
