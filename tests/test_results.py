import json
import os

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


def test_a_teardown_error_is_a_failed_test_explained_to_pytest(pytester, tmp_path, monkeypatch):
    # fastci retries what results.failed holds, so a teardown error is retried like
    # a call failure; a test failing in call and teardown is one test, two reports.
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    pytester.makepyfile(
        test_t="""
import pytest
@pytest.fixture
def leaky():
    yield
    raise RuntimeError("left a server running")
def test_clean_call(leaky): pass
def test_both(leaky): assert 0
def test_ok(): pass
"""
    )
    pytester.runpytest_subprocess("-p", "fastci.python.plugin")
    r = results.read(str(out), signalled=False)
    assert r.failed == ["test_t.py::test_clean_call", "test_t.py::test_both"]
    assert r.errors["test_t.py::test_clean_call"].startswith("RuntimeError: left a server")
    assert r.passed == {"test_t.py::test_ok"}
    assert r.outcomes["test_t.py::test_clean_call"] == "failed"
    assert (r.failed_reports, r.testsfailed, r.explained) == (3, 3, True)


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


FLAKY = """
import pytest
@pytest.fixture
def broken():
    raise RuntimeError("fixture timed out")
def test_flaky(): assert 0, "timed out"
def test_bad(): assert 0
@pytest.mark.parametrize("x", ["a b/c", "d"])
def test_p(x): assert 0
def test_setup(broken): pass
def test_lucky(): pass
"""


def _flag(tmp_path, monkeypatch, flagged: dict) -> str:
    out = tmp_path / "r.jsonl"
    listed = tmp_path / "flagged.json"
    listed.write_text(json.dumps(flagged), encoding="utf-8")
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    monkeypatch.setenv("FASTCI_FLAGGED", str(listed))
    return str(out)


def test_a_flagged_failure_does_not_fail_pytest(pytester, tmp_path, monkeypatch):
    out = _flag(
        tmp_path,
        monkeypatch,
        {
            "test_f.py::test_flaky": 31,
            "test_f.py::test_p[a b/c]": None,
            "test_f.py::test_setup": 9,
            "test_f.py::test_lucky": 4,
        },
    )
    pytester.makepyfile(test_f=FLAKY)
    # --maxfail=1 goes on past the flagged failures to the real one, then stops.
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist")
    p.assert_outcomes(passed=1, failed=2, xfailed=3)
    os.remove(out)
    p = pytester.runpytest_subprocess(
        "-p", "fastci.python.plugin", "-p", "no:xdist", "-rx", "--maxfail=1", "-k", "flaky or bad"
    )
    p.assert_outcomes(failed=1, xfailed=1)
    p.stdout.fnmatch_lines(["*XFAIL*test_flaky*flagged flaky: #31*"])
    r = results.read(out, signalled=False)
    assert r.failed == ["test_f.py::test_bad"]
    assert r.flagged == {"test_f.py::test_flaky": 31}
    assert r.outcomes["test_f.py::test_flaky"] == "failed"
    assert r.errors["test_f.py::test_flaky"].startswith("AssertionError: timed out")
    assert (r.failed_reports, r.testsfailed, r.explained) == (1, 1, True)


def test_flagged_outcomes_are_the_real_ones(pytester, tmp_path, monkeypatch):
    out = _flag(
        tmp_path,
        monkeypatch,
        {
            "test_f.py::test_flaky": 31,
            "test_f.py::test_p[a b/c]": None,
            "test_f.py::test_setup": 9,
            "test_f.py::test_lucky": 4,
        },
    )
    pytester.makepyfile(test_f=FLAKY)
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist", "-rx")
    p.stdout.fnmatch_lines(["*XFAIL*test_p?a b/c?*flagged flaky*"])
    r = results.read(out, signalled=False)
    assert r.flagged == {
        "test_f.py::test_flaky": 31,
        "test_f.py::test_p[a b/c]": None,
        "test_f.py::test_setup": 9,
        "test_f.py::test_lucky": 4,
    }
    # Only the exact id is flagged: test_p[d] fails the run.
    assert r.failed == ["test_f.py::test_bad", "test_f.py::test_p[d]"]
    assert r.outcomes["test_f.py::test_p[a b/c]"] == "failed"
    assert r.outcomes["test_f.py::test_setup"] == "failed"
    assert r.outcomes["test_f.py::test_lucky"] == "passed"
    assert r.passed == {"test_f.py::test_lucky"}
    assert (r.failed_reports, r.testsfailed, r.explained) == (2, 2, True)


def test_a_flagged_pass_is_not_an_error_under_xfail_strict(pytester, tmp_path, monkeypatch):
    out = _flag(tmp_path, monkeypatch, {"test_f.py::test_ok": 31})
    pytester.makeini("[pytest]\nxfail_strict = true\n")
    pytester.makepyfile(test_f="def test_ok(): pass\n")
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist")
    assert p.ret == 0
    p.assert_outcomes(passed=1)
    r = results.read(out, signalled=False)
    assert r.outcomes == {"test_f.py::test_ok": "passed"} and r.flagged == {
        "test_f.py::test_ok": 31
    }


MARKED = """
import pytest
@pytest.mark.quarantine("#776")
def test_str(): assert 0
@pytest.mark.quarantine(12)
def test_int(): assert 0
@pytest.mark.quarantine
def test_bare(): assert 0
@pytest.mark.hold("#5")
def test_other(): assert 0
def test_ok(): pass
"""


def test_the_mark_flags_a_test_with_its_issue(pytester, tmp_path, monkeypatch):
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    monkeypatch.setenv("FASTCI_FLAKY_MARK", "quarantine")
    pytester.makeini("[pytest]\nmarkers =\n    hold: a project's own mark\n")
    pytester.makepyfile(test_m=MARKED)
    p = pytester.runpytest_subprocess(
        "-p", "fastci.python.plugin", "-p", "no:xdist", "-rx", "--strict-markers", "-k", "not other"
    )
    assert p.ret == 0
    p.stdout.fnmatch_lines(["*XFAIL*test_str*flagged flaky: #776*"])
    p.stdout.fnmatch_lines(["*XFAIL*test_bare*flagged flaky"])
    r = results.read(str(out), signalled=False)
    assert r.flagged == {
        "test_m.py::test_str": 776,
        "test_m.py::test_int": 12,
        "test_m.py::test_bare": None,
    }


def test_a_plain_pytest_run_flags_nothing(pytester, tmp_path, monkeypatch):
    # A repository that runs its quarantined tests in a job of its own sees them fail:
    # only fastci, which sets FASTCI_FLAKY_MARK or FASTCI_FLAGGED, flags tests.
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    monkeypatch.delenv("FASTCI_FLAKY_MARK", raising=False)
    monkeypatch.delenv("FASTCI_FLAGGED", raising=False)
    pytester.makeini("[pytest]\nmarkers =\n    hold: a project's own mark\n")
    pytester.makepyfile(test_m=MARKED)
    p = pytester.runpytest_subprocess(
        "-p", "fastci.python.plugin", "-p", "no:xdist", "--strict-markers", "-k", "not other"
    )
    p.assert_outcomes(passed=1, failed=3)
    r = results.read(str(out), signalled=False)
    assert r.flagged == {} and len(r.failed) == 3


@pytest.mark.parametrize("own", ["always", "slow", "gpu", "browser", "e2e", "bench"])
def test_a_mark_naming_one_of_fastcis_own_flags_nothing(pytester, tmp_path, monkeypatch, own):
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    monkeypatch.setenv("FASTCI_FLAKY_MARK", own)
    pytester.makepyfile(test_m=f"import pytest\n@pytest.mark.{own}\ndef test_x(): assert 0\n")
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist", "--markers")
    p.stdout.fnmatch_lines([f"@pytest.mark.{own}: *fastci*"])
    assert "a known flaky test" not in p.stdout.str()
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist")
    p.assert_outcomes(failed=1)
    r = results.read(str(out), signalled=False)
    assert r.flagged == {} and r.failed == ["test_m.py::test_x"]


@pytest.mark.parametrize(
    "name,mark",
    [
        ("skipif", "skipif(False, reason='no')"),
        ("usefixtures", "usefixtures()"),
        ("filterwarnings", "filterwarnings('ignore')"),
        ("parametrize", "parametrize('x', [1])"),
    ],
)
def test_a_mark_naming_one_of_pytests_own_flags_nothing(
    pytester, tmp_path, monkeypatch, name, mark
):
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    monkeypatch.setenv("FASTCI_FLAKY_MARK", name)
    x = "x" if name == "parametrize" else ""
    pytester.makepyfile(test_m=f"import pytest\n@pytest.mark.{mark}\ndef test_x({x}): assert 0\n")
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist", "--markers")
    assert "a known flaky test" not in p.stdout.str()
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist")
    p.assert_outcomes(failed=1)
    assert results.read(str(out), signalled=False).flagged == {}


def test_the_mark_name_is_configured(pytester, tmp_path, monkeypatch):
    out = tmp_path / "r.jsonl"
    monkeypatch.setenv("FASTCI_RESULTS", str(out))
    monkeypatch.setenv("FASTCI_FLAKY_MARK", "hold")
    pytester.makepyfile(test_m=MARKED)
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist", "--markers")
    p.stdout.fnmatch_lines(["@pytest.mark.hold: a known flaky test*"])
    p = pytester.runpytest_subprocess(
        "-p", "fastci.python.plugin", "-p", "no:xdist", "-k", "other or ok"
    )
    assert p.ret == 0
    r = results.read(str(out), signalled=False)
    assert r.flagged == {"test_m.py::test_other": 5}


def test_skip_flagged_deselects_them(pytester, tmp_path, monkeypatch):
    out = _flag(tmp_path, monkeypatch, {"test_m.py::test_ok": 3})
    monkeypatch.setenv("FASTCI_SKIP_FLAGGED", "1")
    pytester.makepyfile(test_m=MARKED)
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-p", "no:xdist")
    p.assert_outcomes(failed=1, deselected=4)
    r = results.read(out, signalled=False)
    assert r.failed == ["test_m.py::test_other"] and r.flagged == {}


def test_a_flagged_failure_under_xdist(pytester, tmp_path, monkeypatch):
    pytest.importorskip("xdist")
    out = _flag(tmp_path, monkeypatch, {"test_f.py::test_flaky": 31})
    pytester.makepyfile(test_f="def test_flaky(): assert 0\ndef test_ok(): pass\n")
    p = pytester.runpytest_subprocess("-p", "fastci.python.plugin", "-n", "2")
    assert p.ret == 0
    r = results.read(out, signalled=False)
    assert r.flagged == {"test_f.py::test_flaky": 31} and r.failed == []
    assert r.outcomes == {"test_f.py::test_flaky": "failed", "test_f.py::test_ok": "passed"}
    assert r.explained


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
