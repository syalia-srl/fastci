import pytest
from health.core import long_path


@pytest.mark.parametrize("n", [1, 2, 3])
def test_cases(n):
    assert long_path(False) + n == n
