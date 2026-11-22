import time

from health.core import short


def test_waits():
    time.sleep(0.6)
    assert short() == 3
