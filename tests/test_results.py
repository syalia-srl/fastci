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


def test_a_pytest_started_inside_an_xdist_worker_writes_its_results(
    pytester, tmp_path, monkeypatch
):
    # fastci's own suite runs under -n: the inner pytest inherits the worker's variable.
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    pytester.makepyfile(test_a="def test_ok(): pass\ndef test_bad(): assert 0\n")
    pytester.runpytest_subprocess("-p", "fastci.python.plugin")
    r = results.read(str(out), signalled=False)
    assert r is not None and r.failed == ["test_a.py::test_bad"]
    assert r.passed == {"test_a.py::test_ok"}


@pytest.mark.parametrize(
    "args,testsfailed,stopped",
    [
        ([], 1, False),
        (["-o", "addopts=-x"], 1, True),
        (["--continue-on-collection-errors"], 2, False),
        (["--cov=.", "--cov-fail-under=100", "-p", "pytest_cov"], 2, False),
    ],
)
def test_the_session_line_says_what_pytest_counted(
    pytester, tmp_path, monkeypatch, args, testsfailed, stopped
):
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    pytester.makepyfile(test_a="def test_bad(): assert 0\ndef test_ok(): pass\n")
    if "--continue-on-collection-errors" in args:
        pytester.makepyfile(test_b="raise ImportError('broken')")
    if "--cov=." in args:
        pytester.makepyfile(lib="def unused():\n    return 1\n")
    pytester.runpytest_subprocess("-p", "fastci.python.plugin", *args)
    r = results.read(str(out), signalled=False)
    assert r.failed == ["test_a.py::test_bad"] and r.failed_reports == 1
    assert (r.testsfailed, r.stopped) == (testsfailed, stopped)
    assert r.explained is (testsfailed == 1)
    assert ("test_a.py::test_ok" in r.passed) is not stopped


def test_no_file_is_none(tmp_path):
    assert results.read(str(tmp_path / "missing"), signalled=False) is None


@pytest.mark.parametrize(
    "code,yes",
    [
        (-11, True),
        (139, True),
        (135, True),
        (137, True),
        (-6, True),
        (-4, True),
        (136, True),
        (-15, False),
        (-2, False),
        (143, False),
        (1, False),
        (2, False),
        (130, False),
    ],
)
def test_signalled(code, yes):
    assert results.signalled(code) is yes
