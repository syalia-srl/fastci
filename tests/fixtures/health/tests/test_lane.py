import pytest


@pytest.mark.slow
def test_slow():
    pass


@pytest.mark.skip(reason="needs a gpu")
def test_skip():
    pass
