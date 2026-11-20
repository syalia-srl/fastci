import gzip
import json
import urllib.error

import pytest

from fastci import execute as ex
from fastci import fetch
from fastci.adapter import Project
from fastci.config import FlakyConfig, default_tiers
from fastci.flaky import FlakyRun, Reach
from fastci.plan import Step
from fastci.python.mapfile import TestMap
from fastci.tier import detect

PROJ = Project("fake", ".", frozenset({"pytest"}), ())


class FakeAdapter:
    """Returns ``code``, or each call the next ``(code, events)`` of ``script``,
    writing the events to the results file the call was given."""

    name = "fake"

    def __init__(self, code=0, script=()):
        self.code, self.calls, self.script, self.done = code, [], list(script), []

    def run_tests(self, project, repo, select, args, env, wrap=lambda a: a):
        self.calls.append((select, args, wrap(["pytest"])))
        if "FASTCI_DONE" in env:
            with open(env["FASTCI_DONE"]) as f:
                self.done.append(set(f.read().split()))
        if not self.script:
            return self.code
        code, events = self.script.pop(0)
        if events and "FASTCI_RESULTS" in env:
            with open(env["FASTCI_RESULTS"], "w") as f:
                f.writelines(json.dumps(e) + "\n" for e in events)
        return code


def events(passed=(), failed=(), hung=(), stopped=False, testsfailed=None):
    """Results-file lines: tests that passed, failed, or started and never finished,
    then the session line pytest writes when it ends (not when a test killed it).
    ``testsfailed`` defaults to the failed tests: pytest counted nothing else."""
    out = []
    for t in passed:
        out += [{"e": "started", "id": t}, outcome(t, "passed"), {"e": "finished", "id": t}]
    for t in failed:
        out += [{"e": "started", "id": t}, outcome(t, "failed"), {"e": "finished", "id": t}]
    if hung:
        return out + [{"e": "started", "id": t} for t in hung]
    n = len(failed) if testsfailed is None else testsfailed
    return out + [{"e": "session", "testsfailed": n, "stopped": stopped, "status": int(n > 0)}]


def outcome(t, result):
    return {"e": "outcome", "id": t, "when": "call", "outcome": result, "error": "boom", "dur": 0}


def check(name, cmd):
    return Step(name, ".", "run", ["sh", "-c", cmd], None, "")


def tests_step():
    return Step("tests", ".", "run", ["-q"], ["tests/test_a.py"], "")


def run(steps, adapter, **kw):
    return ex.execute(steps, "/tmp", {"fake": adapter}, {".": PROJ}, **kw)


def test_stops_at_first_failure_and_names_the_rest(capsys):
    a = FakeAdapter()
    steps = [check("lint", "false"), check("types", "true"), tests_step()]
    assert run(steps, a, fail_fast_tests=False) == 1
    assert a.calls == []
    err = capsys.readouterr().err
    assert "fastci: lint failed (exit 1); later stages did not run: types, tests" in err


def test_pytest_exit_5_passes():
    assert run([tests_step()], FakeAdapter(code=5), fail_fast_tests=False) == 0


def test_fail_fast_adds_maxfail():
    a = FakeAdapter()
    run([tests_step()], a, fail_fast_tests=True)
    assert "--maxfail=1" in a.calls[0][1]


def test_wrap_prefixes_commands(tmp_path):
    out = tmp_path / "argv"
    wrap = lambda argv: ["env", f"FASTCI_PROBE={out}", *argv]  # noqa: E731
    steps = [check("lint", 'echo "$FASTCI_PROBE" > "$FASTCI_PROBE"'), tests_step()]
    a = FakeAdapter()
    assert ex.execute(steps, str(tmp_path), {"fake": a}, {".": PROJ}, False, wrap) == 0
    assert out.read_text().strip() == str(out)
    assert a.calls[0][2][:2] == ["env", f"FASTCI_PROBE={out}"]


def test_skipped_steps_print_their_reason(capsys):
    run(
        [Step("format", ".", "skip", [], None, "no changed file matches")],
        FakeAdapter(),
        fail_fast_tests=False,
    )
    assert "fastci: format skipped: no changed file matches" in capsys.readouterr().err


def save_map(path, sha):
    TestMap(sha=sha, tests=[], durations=[], always=set(), files={}, reads={}).save(str(path))


PR = detect({}, "main", default_tiers(), "pr")


def test_ensure_map_skips_download_when_sha_matches(monkeypatch, tmp_path):
    path = tmp_path / ".fastci" / "map.json.gz"
    path.parent.mkdir()
    save_map(path, "abc")
    monkeypatch.setenv("GH_TOKEN", "t")
    art = {"workflow_run": {"head_sha": "abc"}, "archive_download_url": "u"}
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: art)
    monkeypatch.setattr(fetch, "_download", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert ex.ensure_map(str(tmp_path), PR, "o/r", "main") == str(path)


def test_ensure_map_downloads_a_newer_map(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "t")
    art = {"workflow_run": {"head_sha": "new"}, "archive_download_url": "u"}
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: art)
    monkeypatch.setattr(fetch, "_download", lambda url, token, out: save_map(out, "new"))
    path = ex.ensure_map(str(tmp_path), PR, "o/r", "main")
    with gzip.open(path) as f:
        assert json.load(f)["sha"] == "new"


def test_ensure_map_returns_none_offline(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GH_TOKEN", "t")

    def boom(*a, **k):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(fetch, "newest_artifact", boom)
    assert ex.ensure_map(str(tmp_path), PR, "o/r", "main") is None
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("fastci: ")


def test_repo_from_remote_url():
    assert ex.repo_from_url("git@github.com:o/r.git") == "o/r"
    assert ex.repo_from_url("https://github.com/o/r") == "o/r"
    assert ex.repo_from_url("/srv/git/r.git") is None


def test_fix_rewrites_then_checks(tmp_path, monkeypatch):
    import subprocess

    from fastci import cli

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n[dependency-groups]\ndev = ["ruff"]\n'
    )
    (tmp_path / "a.py").write_text("x=1\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    assert cli.main(["fix"]) == 0
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_a_run_killed_by_the_cap_names_the_setting(capsys):
    note = "the run hit the memory cap of 4.0 GiB; raise [tool.fastci.local] memory"
    assert run([tests_step()], FakeAdapter(code=137), fail_fast_tests=False, cap_note=note) == 137
    assert note in capsys.readouterr().err


def test_a_laptop_gives_up_on_github_quickly(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, timeout=60: seen.append(timeout))
    ex.ensure_map(str(tmp_path), detect({}, "main", default_tiers(), "local"), "o/r", "main")
    ex.ensure_map(str(tmp_path), PR, "o/r", "main")
    assert seen[0] == 5 and seen[-1] == 60


NOTHING = Reach(frozenset())


def flaky(retries=1, reach=NOTHING):
    return FlakyRun(FlakyConfig(), retries, reach)


def xdist_step():
    return Step("tests", ".", "run", ["-q", "-n", "4"], ["tests/test_a.py"], "")


def test_a_flake_the_change_does_not_reach_passes(capsys):
    a = FakeAdapter(script=[(1, events(["b"], ["a"])), (0, events(["a"]))])
    assert run([xdist_step()], a, fail_fast_tests=False, flaky=flaky()) == 0
    assert a.calls[1][0] == ["a"] and "-n" not in a.calls[1][1]
    err = capsys.readouterr().err
    assert "fastci: 1 failed; retrying it once, serially, in a fresh process" in err
    assert "a\n          failed, then passed; this change does not reach it" in err


def test_a_reached_flake_is_red(capsys):
    reach = Reach(frozenset({"a"}), why={"a": "src/up.py:resume"})
    a = FakeAdapter(script=[(1, events(["b"], ["a"])), (0, events(["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky(reach=reach)) == 1
    err = capsys.readouterr().err
    assert "this change reaches it through src/up.py:resume" in err
    assert "check for a race in what the change touched" in err


GREEN = "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432"


def test_on_main_a_forgiven_flake_says_no_commit_since_green_reaches_it(capsys):
    a = FakeAdapter(script=[(1, events([], ["a"])), (0, events(["a"]))])
    assert (
        run(
            [tests_step()],
            a,
            fail_fast_tests=False,
            flaky=flaky(reach=Reach(frozenset(), since=GREEN)),
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "a\n          failed, then passed; no commit since the last green run reaches it" in err
    assert "this change" not in err


def test_on_main_a_reached_flake_names_the_commits_since_green(capsys):
    reach = Reach(frozenset({"a"}), why={"a": "src/up.py:resume"}, since=GREEN)
    a = FakeAdapter(script=[(1, events([], ["a"])), (0, events(["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky(reach=reach)) == 1
    err = capsys.readouterr().err
    assert "failed, then passed; the commits since 9f8e7d6 reach it through src/up.py:resume" in err
    assert "red: check for a race in what those commits touched" in err
    assert "this change" not in err and "the change" not in err


def test_a_flake_nothing_can_judge_is_red(capsys):
    a = FakeAdapter(script=[(1, events([], ["a"])), (0, events(["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky(reach=Reach.everything())) == 1
    assert "fastci cannot tell what this change reaches" in capsys.readouterr().err


def test_a_failure_that_fails_again_is_red(capsys):
    a = FakeAdapter(script=[(1, events(["b"], ["a"])), (1, events([], ["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == 1
    assert "a\n          failed, then failed again on retry" in capsys.readouterr().err


def test_every_retry_round_gets_a_try():
    a = FakeAdapter(script=[(1, events([], ["a"])), (1, events([], ["a"])), (0, events(["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky(retries=2)) == 0
    assert len(a.calls) == 3


def test_three_flakes_in_one_run_look_like_the_machine(capsys):
    ids = ["a", "b", "c"]
    a = FakeAdapter(script=[(1, events([], ids)), (0, events(ids))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == 1
    err = capsys.readouterr().err
    assert "3 tests failed and then passed: this looks like the machine, not the code" in err


def test_a_crashed_test_is_red_and_not_retried(capsys):
    a = FakeAdapter(script=[(-7, events(["b"], hung=["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == -7
    assert len(a.calls) == 1
    assert "a\n          killed its process with SIGBUS" in capsys.readouterr().err


def test_retries_zero_is_todays_behaviour(capsys):
    a = FakeAdapter(script=[(1, events([], ["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky(retries=0)) == 1
    assert len(a.calls) == 1
    assert "retrying" not in capsys.readouterr().err


def test_no_results_file_means_the_exit_code():
    a = FakeAdapter(script=[(2, None)])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == 2
    assert len(a.calls) == 1


def test_a_cap_kill_blames_no_test(capsys):
    note = "the run hit the memory cap of 4.0 GiB; raise [tool.fastci.local] memory"
    a = FakeAdapter(script=[(-9, events(["b"], hung=["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, cap_note=note, flaky=flaky()) == -9
    err = capsys.readouterr().err
    assert note in err and "killed its process" not in err
    assert len(a.calls) == 1


def test_fail_fast_resumes_after_a_forgiven_flake():
    first = events(["b"], ["a"], stopped=True)
    a = FakeAdapter(script=[(1, first), (0, events(["a"])), (0, events(["c"]))])
    assert run([tests_step()], a, fail_fast_tests=True, flaky=flaky()) == 0
    assert len(a.calls) == 3 and a.done == [{"a", "b"}]
    assert a.calls[2][0] == ["tests/test_a.py"] and "--maxfail=1" in a.calls[2][1]


@pytest.mark.parametrize("code", [2, 3, 4])
def test_forgiven_failures_do_not_hide_a_pytest_error(code):
    a = FakeAdapter(script=[(code, events(["b"], ["a"])), (0, events(["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == code
    assert len(a.calls) == 1


STOP_EARLY = [["--maxfail=1"], ["--maxfail=3"], ["--maxfail", "3"], ["-x"], ["--exitfirst"]]


@pytest.mark.parametrize("stop", STOP_EARLY)
def test_a_retry_round_runs_every_failed_test(stop):
    step = Step("tests", ".", "run", ["-q", *stop, "-v"], ["tests/test_a.py"], "")
    a = FakeAdapter(script=[(1, events([], ["a", "b"])), (0, events(["a", "b"])), (0, [])])
    assert run([step], a, fail_fast_tests=False, flaky=flaky()) == 0
    assert a.calls[1][1] == ["-q", "-v", "--maxfail=0"]


@pytest.mark.parametrize("stop", STOP_EARLY)
def test_any_stop_early_flag_resumes_after_a_forgiven_flake(stop):
    step = Step("tests", ".", "run", ["-q", *stop], ["tests/test_a.py"], "")
    first = events(["b"], ["a"], stopped=True)
    a = FakeAdapter(script=[(1, first), (0, events(["a"])), (0, events(["c"]))])
    assert run([step], a, fail_fast_tests=False, flaky=flaky()) == 0
    assert len(a.calls) == 3 and a.done == [{"a", "b"}]


def test_a_session_pytest_stopped_resumes_whatever_the_stage_arguments():
    # -x in addopts, PYTEST_ADDOPTS or a combined -xq: only pytest knows it stopped.
    first = events(["b"], ["a"], stopped=True)
    a = FakeAdapter(script=[(1, first), (0, events(["a"])), (1, events([], ["c"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == 1
    assert len(a.calls) == 4 and a.done == [{"a", "b"}]


def test_a_session_that_ran_everything_does_not_resume():
    a = FakeAdapter(script=[(1, events(["b"], ["a"])), (0, events(["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == 0
    assert len(a.calls) == 2


# testsfailed 2: a coverage threshold or a collection error; no session line: pytest
# never said what it counted.
@pytest.mark.parametrize("first", [events(["b"], ["a"], testsfailed=2), events(["b"], ["a"])[:-1]])
def test_a_failure_no_failed_test_explains_keeps_the_exit_code(capsys, first):
    a = FakeAdapter(script=[(1, first), (0, events(["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == 1
    assert "pytest counted failures no failed test explains" in capsys.readouterr().err
