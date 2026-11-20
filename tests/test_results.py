import pytest

from fastci import results

DIES = """
import os, signal
def test_first(): pass
def test_dies(): os.kill(os.getpid(), signal.SIGSEGV)
"""


def test_results_record_pass_fail_and_setup_error(pytester, tmp_path, monkeypatch):
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    pytester.makepyfile(
        test_a="""
import pytest
@pytest.fixture
def broken():
    raise RuntimeError("fixture timed out")
def test_ok(): pass
def test_bad(): assert 1 == 2
def test_setup(broken): pass
@pytest.mark.parametrize("x", ["svc-api", "a b/c", "ñ"])
def test_p(x): pass
def test_ñandú(): pass
"""
    )
    pytester.runpytest_subprocess("-p", "fastci.python.plugin")
    r = results.read(str(out), signalled=False)
    assert r.failed == ["test_a.py::test_bad", "test_a.py::test_setup"]
    assert "test_a.py::test_p[a b/c]" in r.passed and "test_a.py::test_p[svc-api]" in r.passed
    # pytest itself escapes non-ASCII parameter ids; a function name stays as written.
    assert "test_a.py::test_p[\\xf1]" in r.passed and "test_a.py::test_ñandú" in r.passed
    assert "test_a.py::test_ok" in r.passed
    assert r.errors["test_a.py::test_setup"].startswith("RuntimeError: fixture timed out")
    assert r.outcomes["test_a.py::test_setup"] == "failed"
    assert r.outcomes["test_a.py::test_ok"] == "passed"
    assert r.crashed == set()


def test_skipped_and_xfailed_tests_have_no_outcome(pytester, tmp_path, monkeypatch):
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    pytester.makepyfile(
        test_s="""
import pytest
def test_ok(): pass
@pytest.mark.skip
def test_marked(): pass
def test_inside(): pytest.skip("not here")
@pytest.mark.xfail
def test_xfail(): assert 0
"""
    )
    pytester.runpytest_subprocess("-p", "fastci.python.plugin")
    r = results.read(str(out), signalled=False)
    assert r.outcomes == {"test_s.py::test_ok": "passed"}
    assert r.passed == {"test_s.py::test_ok"}
    assert r.failed == []


def test_a_test_that_kills_pytest_is_the_crashed_one(pytester, tmp_path, monkeypatch):
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    pytester.makepyfile(test_b=DIES)
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin")
    assert results.signalled(p.ret)
    r = results.read(str(out), signalled=True)
    assert r.crashed == {"test_b.py::test_dies"} and "test_b.py::test_first" in r.passed


def test_a_lost_xdist_worker_names_its_test(pytester, tmp_path, monkeypatch):
    pytest.importorskip("xdist")
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    pytester.makepyfile(test_b=DIES)
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-n", "2")
    assert not results.signalled(p.ret)
    r = results.read(str(out), signalled=False)
    assert r.crashed == {"test_b.py::test_dies"}
    assert "test_b.py::test_first" in r.passed
    # The controller writes each event once; the workers write nothing.
    assert out.read_text().count('"started"') == 2


def test_no_file_is_none(tmp_path):
    assert results.read(str(tmp_path / "missing"), signalled=False) is None


@pytest.mark.parametrize(
    "code,yes",
    [(-11, True), (139, True), (135, True), (137, True), (1, False), (2, False), (130, False)],
)
def test_signalled(code, yes):
    assert results.signalled(code) is yes
