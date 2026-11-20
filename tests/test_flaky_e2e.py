"""A test that fails once and then passes, run by fastci on the sample project."""

import os
import subprocess
import sys

import pytest
from conftest import commit, run_testmap, sh

from fastci import cli, fetch

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


@pytest.fixture
def on_main(sample, tmp_path, monkeypatch):
    """The sample with a recorded map and the flaky test, run as main in CI; set
    ``green`` to the sha the last green run was at."""
    marker = tmp_path / "marker"
    marker.touch()
    (sample / "tests" / "test_once.py").write_text(FLAKY.format(marker=str(marker)))
    sh(sample, "git", "add", "tests/test_once.py")
    commit(sample, "add a flaky test")
    code, out, err = run_testmap(sample, "record", "--", "-q", "-p", "no:cacheprovider")
    assert code == 0, err + out
    for k in [k for k in os.environ if k.startswith("FASTCI_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", "o/r/.github/workflows/fastci.yml@refs/heads/main")
    monkeypatch.setenv("GITHUB_RUN_ID", "9")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: None)
    monkeypatch.chdir(sample)
    state = {"green": None, "asked": []}

    def last_green(repo, branch, path, token, exclude_run, timeout=60):
        state["asked"].append((repo, branch, path, exclude_run))
        return state["green"]

    monkeypatch.setattr(fetch, "last_green_sha", last_green)
    state["head"] = lambda: sh(sample, "git", "rev-parse", "HEAD").strip()
    state["marker"] = marker
    return state


def test_main_keeps_a_flake_red_when_a_commit_since_green_reaches_it(on_main, sample, capsys):
    on_main["green"] = on_main["head"]()
    once = sample / "tests" / "test_once.py"
    once.write_text(once.read_text().replace("fails until", "fails once until"))
    commit(sample)
    on_main["marker"].unlink()
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert on_main["asked"] == [("o/r", "main", ".github/workflows/fastci.yml", "9")]
    assert "this change reaches it through tests/test_once.py" in err


def test_main_forgives_a_flake_no_commit_since_green_reaches(on_main, sample, capsys):
    on_main["green"] = on_main["head"]()
    calc = sample / "src" / "sample" / "calc.py"
    calc.write_text(calc.read_text().replace("return a + b", "return b + a"))
    commit(sample)
    on_main["marker"].unlink()
    assert cli.main([]) == 0
    assert "failed, then passed; this change does not reach it" in capsys.readouterr().err


def test_main_reaches_changed_test_files_when_every_test_was_picked(on_main, sample, capsys):
    on_main["green"] = on_main["head"]()
    (sample / "pyproject.toml").write_text((sample / "pyproject.toml").read_text() + "\n")
    once = sample / "tests" / "test_once.py"
    once.write_text(once.read_text().replace("fails until", "fails once until"))
    commit(sample)
    on_main["marker"].unlink()
    assert cli.main([]) == 1
    assert "this change reaches it through tests/test_once.py" in capsys.readouterr().err


@pytest.mark.parametrize(
    "green,why",
    [
        (None, "no green run of .github/workflows/fastci.yml on main"),
        ("0" * 40, "git does not have 0000000000, the last green run on main"),
    ],
)
def test_main_judges_everything_reached_without_a_green_sha(on_main, capsys, green, why):
    on_main["green"] = green
    on_main["marker"].unlink()
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert f"fastci: {why}; every test counts as reached" in err
    assert "fastci cannot tell what this change reaches" in err


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
