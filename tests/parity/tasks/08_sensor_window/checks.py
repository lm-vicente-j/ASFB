from telemetry import moving_average


def test_every_full_window_is_averaged():
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]


def test_window_equal_to_length_gives_one_value():
    assert moving_average([1, 2, 3, 4], 4) == [2.5]


def test_window_longer_than_input_gives_empty_list():
    assert moving_average([1, 2, 3, 4], 5) == []


def test_window_of_one_returns_readings_as_floats():
    assert moving_average([2, 4, 6], 1) == [2.0, 4.0, 6.0]


def test_empty_input_gives_empty_list():
    assert moving_average([], 3) == []


def test_three_sample_window():
    assert moving_average([10, 20, 30, 40, 50], 3) == [20.0, 30.0, 40.0]
