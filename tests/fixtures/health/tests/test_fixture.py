import time

import pytest
from health.core import short


@pytest.fixture
def warm():
    time.sleep(0.1)
    return 1


def test_one(warm):
    assert short() == 3


def test_two(warm):
    assert short() + warm == 4


def test_three(warm):
    assert short() - warm == 2
