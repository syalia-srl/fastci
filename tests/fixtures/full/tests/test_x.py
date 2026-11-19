import pytest


def test_passes():
    assert True


@pytest.mark.gpu
def test_on_gpu():
    assert True
