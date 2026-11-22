from health.core import long_path


def test_plain():
    assert long_path(False) == 0


def test_flag():
    assert long_path(True) == 1
