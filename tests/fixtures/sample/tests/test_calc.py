from sample.calc import add, mul, root


def test_add():
    assert add(2, 3) == 5


def test_mul():
    assert mul(2, 3) == 6


def test_root():
    assert root(9) == 3
