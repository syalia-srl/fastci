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


def fastci(repo, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FASTCI_", "GITHUB_"))}
    env["CI"] = "true"
    env.update(extra)
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


LATER = """import os

import pytest


@pytest.mark.always
def test_later():
    assert not os.path.exists({later!r})
"""

BROKEN = """import os

if os.path.exists({broken!r}):
    raise ImportError("this module is broken")


def test_fine():
    pass
"""


@pytest.fixture
def forgiven(sample, tmp_path):
    """A recorded sample whose last commit cannot reach the flaky test; touching
    ``later`` fails test_later, touching ``broken`` breaks test_zbroken's import."""
    marker, broken = tmp_path / "marker", tmp_path / "broken"
    tests = sample / "tests"
    (tests / "test_once.py").write_text(FLAKY.format(marker=str(marker)))
    (tests / "test_zlater.py").write_text(LATER.format(later=str(tmp_path / "later")))
    (tests / "test_zbroken.py").write_text(BROKEN.format(broken=str(broken)))
    sh(sample, "git", "add", "tests")
    commit(sample, "add a flaky test")
    marker.touch()
    code, out, err = run_testmap(sample, "record", "--", "-q", "-p", "no:cacheprovider")
    assert code == 0, err + out
    calc = sample / "src" / "sample" / "calc.py"
    calc.write_text(calc.read_text().replace("return a + b", "return b + a"))
    commit(sample)
    marker.unlink()
    return tmp_path


def test_a_stop_early_flag_pytest_reads_elsewhere_still_runs_the_rest(sample, forgiven):
    (forgiven / "later").touch()
    p = fastci(sample, PYTEST_ADDOPTS="-x -p no:cacheprovider")
    assert p.returncode == 1, p.stderr + p.stdout
    assert "test_once\n          failed, then passed; this change does not reach it" in p.stderr
    assert "test_zlater.py::test_later\n          failed, then failed again" in p.stderr


def test_a_forgiven_flake_does_not_hide_a_collection_error(sample, forgiven):
    (forgiven / "broken").touch()
    p = fastci(sample, PYTEST_ADDOPTS="--continue-on-collection-errors -p no:cacheprovider")
    assert p.returncode == 1, p.stderr + p.stdout
    # The always stage itself stays red; the tests stage would catch it with no flake.
    assert "always failed (exit 1); later stages did not run: tests" in p.stderr
    assert "test_once\n          failed, then passed; this change does not reach it" in p.stderr


@pytest.fixture
def on_main(sample, tmp_path, monkeypatch):
    """The sample with a recorded map and the flaky test, run as main in CI; set
    ``green`` to the sha the last green run was at, or to the green runs' shas,
    newest first."""
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

    def green_shas(repo, branch, workflow, token, exclude_run, timeout=60):
        state["asked"].append((repo, branch, workflow, exclude_run))
        green = state["green"]
        if isinstance(green, Exception):
            raise green
        return green if isinstance(green, list) else [green] if green else []

    monkeypatch.setattr(fetch, "green_shas", green_shas)
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
    assert on_main["asked"] == [("o/r", "main", "fastci.yml", "9")]
    green = on_main["green"][:7]
    assert f"the commits since {green} reach it through tests/test_once.py" in err
    assert "red: check for a race in what those commits touched" in err


def test_main_forgives_a_flake_no_commit_since_green_reaches(on_main, sample, capsys):
    on_main["green"] = on_main["head"]()
    calc = sample / "src" / "sample" / "calc.py"
    calc.write_text(calc.read_text().replace("return a + b", "return b + a"))
    commit(sample)
    on_main["marker"].unlink()
    assert cli.main([]) == 0
    err = capsys.readouterr().err
    assert "failed, then passed; no commit since the last green run reaches it" in err


def test_main_reaches_every_test_when_a_full_path_changed(on_main, sample, capsys):
    on_main["green"] = on_main["head"]()
    (sample / "pyproject.toml").write_text((sample / "pyproject.toml").read_text() + "\n")
    commit(sample)
    on_main["marker"].unlink()
    assert cli.main([]) == 1
    green = on_main["green"][:7]
    assert f"the commits since {green} reach it through pyproject.toml" in capsys.readouterr().err


@pytest.fixture
def behind_green(on_main, sample):
    """HEAD changes the flaky test, and a later commit on main, with a green run,
    changes only calc.py; the run judges HEAD, checked out behind it."""
    start = on_main["head"]()
    once = sample / "tests" / "test_once.py"
    once.write_text(once.read_text().replace("fails until", "fails once until"))
    commit(sample)
    head = on_main["head"]()
    calc = sample / "src" / "sample" / "calc.py"
    calc.write_text(calc.read_text().replace("return a + b", "return b + a"))
    commit(sample)
    later = on_main["head"]()
    sh(sample, "git", "checkout", "-q", head)
    on_main["marker"].unlink()
    return start, later


def test_main_skips_a_green_run_newer_than_the_commit_it_judges(on_main, behind_green, capsys):
    start, later = behind_green
    on_main["green"] = [later, start]
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert f"judged against the last green run on main, at {start[:10]}" in err
    assert f"the commits since {start[:7]} reach it through tests/test_once.py" in err


def test_main_reaches_everything_when_no_green_run_is_behind_it(on_main, behind_green, capsys):
    _, later = behind_green
    on_main["green"] = [later]
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert "no green run of fastci.yml on main before this commit in this clone" in err
    assert "every test counts as reached" in err


@pytest.mark.parametrize(
    "green,why",
    [
        (None, "no green run of fastci.yml on main"),
        ("0" * 40, "no green run of fastci.yml on main before this commit in this clone"),
        (OSError("offline"), "could not find the last green run (offline)"),
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
