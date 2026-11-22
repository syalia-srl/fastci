import health.early
import health.untested  # noqa: F401
import pytest


@pytest.mark.always
def test_coherent():
    assert health.early.TABLE["a"] == 1
