from health.core import long_path


def test_a():
    assert long_path(False) == 0


def test_b():
    assert long_path(False) < 1
