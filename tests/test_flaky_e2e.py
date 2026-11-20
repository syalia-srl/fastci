"""A test that fails once and then passes, run by fastci on the sample project."""

import os
import subprocess
import sys

from conftest import commit, run_testmap, sh

FLAKY = """import os

import pytest


@pytest.mark.always
def test_once():
    if not os.path.exists({marker!r}):
        open({marker!r}, "w").close()
        raise AssertionError("fails until the marker exists")
"""


def fastci(repo):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FASTCI_", "GITHUB_"))}
    env["CI"] = "true"
    return subprocess.run(
        [sys.executable, "-m", "fastci.cli", "--tier", "pr", "--base", "HEAD~1"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def test_a_flake_is_forgiven_only_where_the_change_cannot_reach_it(sample, tmp_path):
    marker = tmp_path / "marker"
    (sample / "tests" / "test_once.py").write_text(FLAKY.format(marker=str(marker)))
    sh(sample, "git", "add", "tests/test_once.py")
    commit(sample, "add a flaky test")

    p = fastci(sample)
    assert p.returncode == 1, p.stderr + p.stdout
    assert "fastci cannot tell what this change reaches" in p.stderr

    code, out, err = run_testmap(sample, "record", "--", "-q", "-p", "no:cacheprovider")
    assert code == 0, err + out
    calc = sample / "src" / "sample" / "calc.py"
    calc.write_text(calc.read_text().replace("return a + b", "return b + a"))
    commit(sample)
    marker.unlink()

    p = fastci(sample)
    assert p.returncode == 0, p.stderr + p.stdout
    assert (
        "tests/test_once.py::test_once\n          failed, then passed;"
        " this change does not reach it" in p.stderr
    )


def test_done_tests_are_deselected_within_the_selection(pytester, monkeypatch):
    pytester.makepyfile(test_a="def test_1(): pass\ndef test_2(): pass\ndef test_3(): pass\n")
    sel, done = pytester.path / "sel.txt", pytester.path / "done.txt"
    sel.write_text("test_a.py::test_1\ntest_a.py::test_2\n")
    done.write_text("test_a.py::test_1\n")
    monkeypatch.setenv("FASTCI_SELECT", str(sel))
    monkeypatch.setenv("FASTCI_DONE", str(done))
    r = pytester.runpytest_subprocess("-v", "-p", "fastci.python.plugin")
    r.assert_outcomes(passed=1, deselected=2)
    assert "test_2 PASSED" in r.stdout.str()
