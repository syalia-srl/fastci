import gzip
import io
import json
import os
import urllib.error
import zipfile

import pytest

from fastci import execute as ex
from fastci import fetch, issues
from fastci.adapter import Project
from fastci.config import FlakyConfig, default_tiers
from fastci.flaky import HISTORY, FlakyRun, History, Reach, Verdict
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
        self.envs = []

    def run_tests(self, project, repo, select, args, env, wrap=lambda a: a):
        self.calls.append((select, args, wrap(["pytest"])))
        self.envs.append(dict(env))
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


@pytest.mark.parametrize("code", [-15, -2])
def test_a_run_stopped_by_sigterm_or_sigint_blames_no_test(capsys, code):
    a = FakeAdapter(script=[(code, events(["b"], hung=["a"]))])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=flaky()) == code
    assert "killed its process" not in capsys.readouterr().err


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


# Flagging: fake issues functions record each call to GitHub.

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
RUN = "https://github.com/o/r/actions/runs/4519"


class FakeIssues:
    def __init__(self, monkeypatch, found=None, error=None):
        self.calls, self.found, self.error, self.next = [], dict(found or {}), error, 40
        monkeypatch.setattr(issues, "listed", self.listed)
        for name in ("open_issue", "edit", "comment", "set_open"):
            monkeypatch.setattr(issues, name, self.recorder(name))

    def listed(self, repo, token, label, timeout=60, total=None):
        self.calls.append(("listed", label, timeout, total))
        if self.error:
            raise self.error
        return self.found

    def recorder(self, name):
        def call(repo, token, *args):
            self.calls.append((name, *args))
            if name == "open_issue":
                self.next += 1
                return self.next

        return call

    def writes(self):
        return [c for c in self.calls if c[0] != "listed"]


def in_ci(monkeypatch, ref, event=None):
    """A GitHub Actions run on ``ref``: a push, or a pull_request on refs/pull/."""
    pull = ref.startswith("refs/pull/")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", event or ("pull_request" if pull else "push"))
    monkeypatch.setenv("GITHUB_REF", ref)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("GITHUB_RUN_ID", "4519")
    monkeypatch.setenv("GITHUB_REF_NAME", ref.split("/", 2)[2])


def flagger(tmp_path, tier="pr", token="t", default_branch="main", **cfg):
    ctx = detect(dict(os.environ), default_branch, default_tiers(), tier)
    return ex.Flagger(FlakyConfig(**cfg), ctx, str(tmp_path), "o/r", token, default_branch)


def flake():
    """A test that failed and passed on retry, which the change does not reach."""
    return FakeAdapter(script=[(1, events(["b"], ["t::a"])), (0, events(["t::a"]))])


def run_flagged(f, adapter=None, step=None):
    adapter = adapter or flake()
    fr = FlakyRun(f.cfg, 1, NOTHING, f)
    return run([step or tests_step()], adapter, fail_fast_tests=False, flaky=fr), adapter


def test_a_flake_on_a_pull_request_opens_an_issue_and_comments(monkeypatch, tmp_path, capsys):
    gh = FakeIssues(monkeypatch)
    in_ci(monkeypatch, "refs/pull/7/merge")
    code, a = run_flagged(flagger(tmp_path))
    assert code == 0
    [(op, label, id, body), comment] = gh.writes()
    assert (op, label, id) == ("open_issue", "flaky", "t::a")
    assert issues.marker("t::a") in body.splitlines()
    assert comment == (
        "comment",
        41,
        "Failed and then passed on #7 at a1b2c3d ([run 4519](https://github.com/o/r/actions/runs/4519)).",
    )
    assert a.envs[0]["FASTCI_FLAKY_MARK"] == "quarantine"
    err = capsys.readouterr().err
    # The flag line closes the verdict's own block, as in the spec's "What a run prints".
    block = ex.INDENT.join(
        [
            "fastci: t::a",
            "failed, then passed; this change does not reach it",
            "passed in a fresh process: it may depend on test order",
            "flagged as flaky: #41 (new)",
        ]
    )
    assert block in err and err.count("fastci: t::a") == 1


def test_a_pull_request_comment_cites_the_head_commit(monkeypatch, tmp_path):
    gh = FakeIssues(monkeypatch, {"t::a": issues.Issue(18, True)})
    in_ci(monkeypatch, "refs/pull/7/merge")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "f00ba12" + "0" * 33}}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    run_flagged(flagger(tmp_path))
    assert gh.writes() == [
        (
            "comment",
            18,
            "Failed and then passed on #7 at f00ba12 ([run 4519](https://github.com/o/r/actions/runs/4519)).",
        )
    ]


def test_the_comment_links_the_run_on_the_server_it_ran_on(monkeypatch, tmp_path):
    gh = FakeIssues(monkeypatch, {"t::a": issues.Issue(18, True)})
    in_ci(monkeypatch, "refs/pull/7/merge")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://git.example.org")
    run_flagged(flagger(tmp_path))
    link = "[run 4519](https://git.example.org/o/r/actions/runs/4519)"
    assert gh.writes() == [("comment", 18, f"Failed and then passed on #7 at a1b2c3d ({link}).")]


@pytest.mark.parametrize(
    "ref,event,where",
    [
        ("refs/heads/release/1.2", "push", "release/1.2"),
        ("refs/tags/v1.2.0", "push", "v1.2.0"),
        # main, but not its push: a schedule writes neither the history nor the body.
        ("refs/heads/main", "schedule", "main"),
    ],
)
def test_off_the_default_push_a_flake_opens_its_issue_and_comments(
    monkeypatch, tmp_path, capsys, ref, event, where
):
    gh = FakeIssues(monkeypatch)
    in_ci(monkeypatch, ref, event)
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: pytest.fail("fetched"))
    code, _ = run_flagged(flagger(tmp_path, tier="release"))
    assert code == 0
    assert [c[0] for c in gh.writes()] == ["open_issue", "comment"]
    text = f"Failed and then passed on {where} at a1b2c3d ([run 4519]({RUN}))."
    assert gh.writes()[1] == ("comment", 41, text)
    assert "flagged as flaky: #41 (new)" in capsys.readouterr().err
    assert not (tmp_path / HISTORY).exists()


def test_main_is_the_push_to_the_default_branch_whatever_the_tier(monkeypatch, tmp_path):
    gh = FakeIssues(monkeypatch)
    in_ci(monkeypatch, "refs/heads/trunk")
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: None)
    run_flagged(flagger(tmp_path, tier="release", default_branch="trunk"))
    assert [c[0] for c in gh.writes()] == ["open_issue"]
    assert History.load(tmp_path / HISTORY).tests["t::a"].main == [1]


def test_a_second_flake_of_a_flagged_test_only_comments(monkeypatch, tmp_path):
    gh = FakeIssues(monkeypatch, {"t::a": issues.Issue(18, True)})
    in_ci(monkeypatch, "refs/pull/7/merge")
    code, a = run_flagged(flagger(tmp_path))
    assert code == 0
    assert gh.writes() == [
        (
            "comment",
            18,
            "Failed and then passed on #7 at a1b2c3d ([run 4519](https://github.com/o/r/actions/runs/4519)).",
        )
    ]
    with open(a.envs[0]["FASTCI_FLAGGED"]) as f:
        assert json.load(f) == {"t::a": 18}


def test_a_closed_issue_is_reopened_not_duplicated(monkeypatch, tmp_path):
    gh = FakeIssues(monkeypatch, {"t::a": issues.Issue(18, False)})
    in_ci(monkeypatch, "refs/pull/7/merge")
    code, a = run_flagged(flagger(tmp_path))
    assert code == 0
    assert [c[:3] for c in gh.writes()][:1] == [("set_open", 18, True)]
    assert [c[:2] for c in gh.writes()][1:] == [("comment", 18)]
    # A closed issue flags nothing: the test was judged like any other.
    with open(a.envs[0]["FASTCI_FLAGGED"]) as fh:
        assert json.load(fh) == {}


def history_artifact(monkeypatch, history, member="flaky.json"):
    """newest_artifact finds a fastci-flaky artifact holding ``history``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, json.dumps(history))
    asked = []

    def newest(repo, name, branch, token, timeout=60):
        asked.append((name, branch))
        return {"archive_download_url": "u", "workflow_run": {}}

    class Answer(io.BytesIO):
        headers = {}

    monkeypatch.setattr(fetch, "newest_artifact", newest)
    monkeypatch.setattr(fetch, "_get", lambda *a, **k: Answer(buf.getvalue()))
    return asked


def test_on_main_a_flake_opens_its_issue_and_writes_the_history(monkeypatch, tmp_path):
    gh = FakeIssues(monkeypatch, {"t::f": issues.Issue(5, True)})
    in_ci(monkeypatch, "refs/heads/main")
    old = {"version": 1, "tests": {"t::f": {"main": [1, 0]}}}
    asked = history_artifact(monkeypatch, old)
    first = [*events(["b"], ["t::a"]), *flagged_outcome("t::f", "passed", 5)]
    a = FakeAdapter(script=[(1, first), (0, events(["t::a"]))])
    code, _ = run_flagged(flagger(tmp_path, tier="main"), a)
    assert code == 0 and asked == [("fastci-flaky", "main")]
    h = History.load(tmp_path / HISTORY)
    assert h.tests["t::a"].main == [1] and h.tests["t::a"].last_failure["sha"] == SHA
    assert h.tests["t::f"].main == [1, 0, 0]
    assert h.tests["t::a"].issue == 41
    [opened, edited] = gh.writes()
    assert opened == ("open_issue", "flaky", "t::a", issues.body("t::a", h.tests["t::a"], 20))
    assert edited[:2] == ("edit", 5) and "| main, last 3 | 3 | 1 | 33% |" in edited[2]


def flagged_outcome(t, result, number):
    return [
        {"e": "started", "id": t},
        {**outcome(t, result), "flagged": number},
        {"e": "finished", "id": t},
    ]


def test_on_main_a_closed_issue_is_reopened_and_rewritten(monkeypatch, tmp_path):
    gh = FakeIssues(monkeypatch, {"t::a": issues.Issue(18, False)})
    in_ci(monkeypatch, "refs/heads/main")
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: None)
    run_flagged(flagger(tmp_path, tier="main"))
    assert [c[:2] for c in gh.writes()] == [("set_open", 18), ("edit", 18), ("comment", 18)]
    assert (
        gh.writes()[2][2]
        == f"Failed and then passed again on main at {SHA[:7]} ([run 4519]({RUN}))."
    )


def test_on_main_a_green_run_still_records_the_flagged_tests(monkeypatch, tmp_path):
    FakeIssues(monkeypatch, {"t::f": issues.Issue(5, True)})
    in_ci(monkeypatch, "refs/heads/main")
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: None)
    a = FakeAdapter(script=[(0, [*events(["b"]), *flagged_outcome("t::f", "failed", 5)])])
    code, _ = run_flagged(flagger(tmp_path, tier="main"), a)
    assert code == 0
    assert History.load(tmp_path / HISTORY).tests["t::f"].main == [1]


@pytest.mark.parametrize(
    "outcomes,main",
    [(("passed", "passed"), [0]), (("passed", "failed"), [1]), (("failed", "passed"), [1])],
)
def test_on_main_a_test_run_by_two_stages_is_recorded_once(monkeypatch, tmp_path, outcomes, main):
    # The always stage and the whole suite both run an always-marked test: one run on main.
    FakeIssues(monkeypatch, {"t::f": issues.Issue(5, True)})
    in_ci(monkeypatch, "refs/heads/main")
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: None)
    f = flagger(tmp_path, tier="main")
    a = FakeAdapter(script=[(0, flagged_outcome("t::f", o, 5)) for o in outcomes])
    always = Step("always", ".", "run", ["-m", "always"], None, "")
    fr = FlakyRun(f.cfg, 1, NOTHING, f)
    assert run([always, tests_step()], a, fail_fast_tests=False, flaky=fr) == 0
    assert History.load(tmp_path / HISTORY).tests["t::f"].main == main


def test_on_main_a_flake_in_two_stages_is_recorded_once(monkeypatch, tmp_path):
    FakeIssues(monkeypatch)
    in_ci(monkeypatch, "refs/heads/main")
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: None)
    f = flagger(tmp_path, tier="main")
    flake = [(1, events([], ["t::a"])), (0, events(["t::a"]))]
    always = Step("always", ".", "run", ["-m", "always"], None, "")
    fr = FlakyRun(f.cfg, 1, NOTHING, f)
    assert (
        run([always, tests_step()], FakeAdapter(script=flake * 2), fail_fast_tests=False, flaky=fr)
        == 0
    )
    assert History.load(tmp_path / HISTORY).tests["t::a"].main == [1]


def test_a_history_that_cannot_be_fetched_changes_no_verdict(monkeypatch, tmp_path, capsys):
    gh = FakeIssues(monkeypatch, {"t::a": issues.Issue(18, True)})
    in_ci(monkeypatch, "refs/heads/main")
    history_artifact(monkeypatch, {}, member="other.txt")
    code, _ = run_flagged(flagger(tmp_path, tier="main"))
    assert code == 0
    line = (
        "fastci: could not fetch the flaky history (the artifact holds no flaky.json file);"
        " this run does not write it"
    )
    assert line in capsys.readouterr().err
    # Nothing to upload, so the history main has stays the newest one.
    assert not (tmp_path / HISTORY).exists()
    # A body rendered from no history would wipe the issue's counts.
    assert [c[0] for c in gh.writes()] == []


def test_a_failed_history_download_writes_no_history(monkeypatch, tmp_path, capsys):
    FakeIssues(monkeypatch)
    in_ci(monkeypatch, "refs/heads/main")
    history_artifact(monkeypatch, {})

    def fail(*a, **k):
        raise urllib.error.HTTPError("u", 500, "Server Error", {}, None)

    monkeypatch.setattr(fetch, "_get", fail)
    code, _ = run_flagged(flagger(tmp_path, tier="main"))
    assert code == 0 and not (tmp_path / HISTORY).exists()
    assert "could not fetch the flaky history (HTTP Error 500" in capsys.readouterr().err


def test_a_history_that_does_not_parse_writes_no_history(monkeypatch, tmp_path):
    FakeIssues(monkeypatch)
    in_ci(monkeypatch, "refs/heads/main")
    history_artifact(monkeypatch, [])
    run_flagged(flagger(tmp_path, tier="main"))
    assert not (tmp_path / HISTORY).exists()


def test_a_history_entry_with_a_new_key_is_kept(monkeypatch, tmp_path):
    FakeIssues(monkeypatch)
    in_ci(monkeypatch, "refs/heads/main")
    old = {"version": 1, "tests": {"t::a": {"main": [0, 0], "added_later": 1}}}
    history_artifact(monkeypatch, old)
    run_flagged(flagger(tmp_path, tier="main"))
    assert History.load(tmp_path / HISTORY).tests["t::a"].main == [0, 0, 1]


@pytest.mark.parametrize("ref,tier", [("refs/heads/main", "main"), ("refs/pull/7/merge", "pr")])
def test_retries_zero_turns_flags_off(monkeypatch, tmp_path, ref, tier):
    # A flagged test then blocks like any test: nothing is read, set or written.
    gh = FakeIssues(monkeypatch, {"t::a": issues.Issue(18, True)})
    in_ci(monkeypatch, ref)
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: pytest.fail("fetched"))
    f = flagger(tmp_path, tier=tier, flagged="skip")
    a = FakeAdapter(script=[(1, events([], ["t::a"]))])
    fr = FlakyRun(f.cfg, 0, NOTHING, f)
    assert run([tests_step()], a, fail_fast_tests=False, flaky=fr) == 1
    assert gh.calls == [] and not any(k.startswith("FASTCI_") for k in a.envs[0])
    assert not (tmp_path / HISTORY).exists() and not (tmp_path / ex.FLAGGED).exists()


def test_with_marks_nothing_is_called_and_the_mark_line_prints(monkeypatch, tmp_path, capsys):
    gh = FakeIssues(monkeypatch)
    in_ci(monkeypatch, "refs/pull/7/merge")
    f = flagger(tmp_path, store="marks")
    code, a = run_flagged(f)
    assert code == 0 and gh.calls == []
    assert "FASTCI_FLAGGED" not in a.envs[0] and a.envs[0]["FASTCI_FLAKY_MARK"] == "quarantine"
    line = 'add @pytest.mark.quarantine("#?") to t::a'
    assert f"{ex.INDENT}{line}" in capsys.readouterr().err and line in f.lines


def test_with_both_the_mark_line_names_the_issue(monkeypatch, tmp_path, capsys):
    FakeIssues(monkeypatch, {"t::a": issues.Issue(18, True)})
    in_ci(monkeypatch, "refs/pull/7/merge")
    run_flagged(flagger(tmp_path, store="both"))
    assert 'add @pytest.mark.quarantine("#18") to t::a' in capsys.readouterr().err


def test_a_refused_write_leaves_the_verdict(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(issues, "listed", lambda *a, **k: {})
    in_ci(monkeypatch, "refs/pull/7/merge")

    def refuse(method, url, token, body, timeout=30):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(fetch, "_send", refuse)
    f = flagger(tmp_path)
    code, _ = run_flagged(f)
    assert code == 0
    line = "could not write the flag for t::a: HTTP Error 403: Forbidden"
    assert f"{ex.INDENT}{line}" in capsys.readouterr().err and f.lines == [line]


def test_issues_that_cannot_be_read_write_nothing(monkeypatch, tmp_path, capsys):
    gh = FakeIssues(monkeypatch, error=urllib.error.URLError("offline"))
    in_ci(monkeypatch, "refs/pull/7/merge")
    code, a = run_flagged(flagger(tmp_path))
    assert code == 0 and gh.writes() == [] and "FASTCI_FLAGGED" not in a.envs[0]
    err = capsys.readouterr().err
    assert "could not read the flaky issues" in err
    assert "could not write the flag for t::a" in err


def test_a_laptop_never_writes_and_reads_its_copy_when_offline(monkeypatch, tmp_path, capsys):
    gh = FakeIssues(monkeypatch, error=urllib.error.URLError("offline"))
    copy = tmp_path / ".fastci" / "flagged.json"
    copy.parent.mkdir()
    copy.write_text(json.dumps({"t::f": 5}))
    code, a = run_flagged(flagger(tmp_path, tier="local"))
    assert code == 0 and gh.writes() == [] and gh.calls[0][2:] == (5, 5)
    with open(a.envs[0]["FASTCI_FLAGGED"]) as f:
        assert json.load(f) == {"t::f": 5}
    err = capsys.readouterr().err
    later = "failed and then passed; CI will flag it if it does the same there"
    assert f"order{ex.INDENT}{later}" in err and err.count("fastci: t::a") == 1
    assert not (tmp_path / HISTORY).exists()


def test_a_laptop_refreshes_its_copy(monkeypatch, tmp_path):
    FakeIssues(monkeypatch, {"t::f": issues.Issue(5, True), "t::g": issues.Issue(6, False)})
    run_flagged(flagger(tmp_path, tier="local"))
    assert json.loads((tmp_path / ".fastci" / "flagged.json").read_text()) == {"t::f": 5}


@pytest.mark.parametrize("tier,skip", [("local", True), ("pr", True), ("main", False)])
def test_skip_flagged_only_where_tests_are_picked(monkeypatch, tmp_path, tier, skip):
    FakeIssues(monkeypatch)
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: None)
    _, a = run_flagged(flagger(tmp_path, tier=tier, flagged="skip"), FakeAdapter())
    assert ("FASTCI_SKIP_FLAGGED" in a.envs[0]) is skip


def test_a_resumed_stage_does_not_rerun_a_flagged_test(monkeypatch, tmp_path):
    FakeIssues(monkeypatch, {"t::f": issues.Issue(5, True)})
    first = [*flagged_outcome("t::f", "failed", 5), *events(["b"], ["t::a"], stopped=True)]
    a = FakeAdapter(script=[(1, first), (0, events(["t::a"])), (0, events(["c"]))])
    in_ci(monkeypatch, "refs/pull/7/merge")
    code, _ = run_flagged(flagger(tmp_path), a)
    assert code == 0 and a.done == [{"t::a", "b", "t::f"}]


# Leaving the list, and the job summary.

OPEN_5 = issues.Issue(5, True)
LANDED = "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432"


def run_on_main(monkeypatch, tmp_path, entry, result="passed", reach=NOTHING, issue=OPEN_5, **cfg):
    """One run on main where flagged t::f (#5) has ``result``, with ``entry`` its history."""
    gh = FakeIssues(monkeypatch, {"t::f": issue} if issue else {})
    in_ci(monkeypatch, "refs/heads/main")
    history_artifact(monkeypatch, {"version": 1, "tests": {"t::f": entry}})
    f = flagger(tmp_path, tier="main", **cfg)
    a = FakeAdapter(script=[(0, [*events(["b"]), *flagged_outcome("t::f", result, 5)])])
    code = run([tests_step()], a, fail_fast_tests=False, flaky=FlakyRun(f.cfg, 1, reach, f))
    return code, gh, f, History.load(tmp_path / HISTORY).tests["t::f"]


REACHES_F = Reach(frozenset({"t::f"}))


def test_on_main_the_20th_clean_run_after_a_reaching_change_closes_the_issue(monkeypatch, tmp_path):
    entry = {"main": [0] * 19, "landed": True, "clean": 19, "landed_sha": LANDED}
    code, gh, _, e = run_on_main(monkeypatch, tmp_path, entry)
    assert code == 0
    *_, closed, said = gh.writes()
    assert closed == ("set_open", 5, False)
    assert said == (
        "comment",
        5,
        "Passed 20 runs on main in a row since a change that reaches it landed"
        f" ({LANDED[:7]}). Unflagged.",
    )
    assert (e.landed, e.clean, e.landed_sha) == (False, 0, None)


def test_the_close_comment_leaves_out_a_landing_commit_it_does_not_know(monkeypatch, tmp_path):
    entry = {"main": [0] * 19, "landed": True, "clean": 19}
    _, gh, _, _ = run_on_main(monkeypatch, tmp_path, entry)
    assert gh.writes()[-1] == (
        "comment",
        5,
        "Passed 20 runs on main in a row since a change that reaches it landed. Unflagged.",
    )


@pytest.mark.parametrize(
    "issue,why", [(None, "it has no issue"), (issues.Issue(5, False), "#5 is already closed")]
)
def test_a_due_test_without_an_open_issue_says_its_flag_ends(
    monkeypatch, tmp_path, capsys, issue, why
):
    entry = {"main": [0] * 19, "landed": True, "clean": 19}
    _, gh, f, e = run_on_main(monkeypatch, tmp_path, entry, issue=issue)
    line = f"t::f: unflagged after 20 clean runs on main; {why}"
    assert line in f.lines and f"fastci: {line}" in capsys.readouterr().err
    assert ("set_open", 5, False) not in gh.writes() and (e.landed, e.clean) == (False, 0)


def test_on_main_a_reaching_change_lands_and_counts(monkeypatch, tmp_path):
    _, gh, _, e = run_on_main(monkeypatch, tmp_path, {"main": [1]}, reach=REACHES_F)
    assert (e.landed, e.clean, e.landed_sha) == (True, 1, SHA)
    assert ("set_open", 5, False) not in gh.writes()


@pytest.mark.parametrize(
    "reach",
    [Reach.everything("uv.lock"), Reach(frozenset(), known=frozenset({"other"}))],
    ids=["everything", "unknown-to-the-map"],
)
def test_on_main_only_a_diff_rule_lands_a_flagged_test(monkeypatch, tmp_path, reach):
    # Judging counts both as reached; landing needs a rule that named the test.
    _, gh, _, e = run_on_main(monkeypatch, tmp_path, {"main": [0] * 19, "clean": 0}, reach=reach)
    assert (e.landed, e.clean, e.landed_sha) == (False, 0, None)
    assert ("set_open", 5, False) not in gh.writes()


def test_on_main_a_changed_test_file_lands_its_tests(monkeypatch, tmp_path):
    reach = Reach(frozenset(), frozenset({"t"}), known=frozenset({"t::f"}))
    _, _, _, e = run_on_main(monkeypatch, tmp_path, {"main": [1]}, reach=reach)
    assert (e.landed, e.clean) == (True, 1)


def test_on_main_a_streak_without_a_reaching_change_does_not_count(monkeypatch, tmp_path):
    _, gh, _, e = run_on_main(monkeypatch, tmp_path, {"main": [0] * 30})
    assert (e.landed, e.clean) == (False, 0)
    assert ("set_open", 5, False) not in gh.writes()


def test_on_main_a_flagged_failure_restarts_the_count(monkeypatch, tmp_path):
    entry = {"main": [0] * 19, "landed": True, "clean": 19}
    _, gh, _, e = run_on_main(monkeypatch, tmp_path, entry, "failed")
    assert (e.landed, e.clean) == (True, 0)
    assert ("set_open", 5, False) not in gh.writes()


def test_unflag_after_zero_never_closes(monkeypatch, tmp_path):
    entry = {"main": [0] * 40, "landed": True, "clean": 40}
    _, gh, _, _ = run_on_main(monkeypatch, tmp_path, entry, unflag_after=0)
    assert ("set_open", 5, False) not in gh.writes()


def test_with_marks_the_summary_says_the_mark_can_go(monkeypatch, tmp_path, capsys):
    entry = {"main": [0] * 19, "landed": True, "clean": 19}
    _, gh, f, e = run_on_main(monkeypatch, tmp_path, entry, store="marks")
    assert "the mark on t::f can go" in f.lines
    assert "fastci: the mark on t::f can go" in capsys.readouterr().err
    assert gh.writes() == [] and (e.landed, e.clean) == (False, 0)


def test_a_refused_close_keeps_the_streak_and_posts_nothing(monkeypatch, tmp_path):
    entry = {"main": [0] * 19, "landed": True, "clean": 19}
    gh = FakeIssues(monkeypatch, {"t::f": issues.Issue(5, True)})

    def refuse(repo, token, number, is_open):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(issues, "set_open", refuse)
    in_ci(monkeypatch, "refs/heads/main")
    history_artifact(monkeypatch, {"version": 1, "tests": {"t::f": entry}})
    f = flagger(tmp_path, tier="main")
    a = FakeAdapter(script=[(0, [*events(["b"]), *flagged_outcome("t::f", "passed", 5)])])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=FlakyRun(f.cfg, 1, NOTHING, f)) == 0
    assert any(line.startswith("could not unflag t::f: ") for line in f.lines)
    assert not any(w[0] == "comment" for w in gh.writes())
    assert History.load(tmp_path / HISTORY).tests["t::f"].clean == 20


def test_a_refused_comment_after_the_close_still_unflags(monkeypatch, tmp_path):
    entry = {"main": [0] * 19, "landed": True, "clean": 19}
    gh = FakeIssues(monkeypatch, {"t::f": issues.Issue(5, True)})

    def refuse(repo, token, number, text):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(issues, "comment", refuse)
    in_ci(monkeypatch, "refs/heads/main")
    history_artifact(monkeypatch, {"version": 1, "tests": {"t::f": entry}})
    f = flagger(tmp_path, tier="main")
    a = FakeAdapter(script=[(0, [*events(["b"]), *flagged_outcome("t::f", "passed", 5)])])
    assert run([tests_step()], a, fail_fast_tests=False, flaky=FlakyRun(f.cfg, 1, NOTHING, f)) == 0
    assert ("set_open", 5, False) in gh.writes()
    assert any(line.startswith("could not comment on #5: ") for line in f.lines)
    assert "t::f: unflagged, #5 closed" in f.lines
    assert History.load(tmp_path / HISTORY).tests["t::f"].clean == 0


def test_the_summary_has_one_row_per_judged_test_with_its_issue_link():
    verdicts = [
        Verdict("t::a", "flaky", False),
        Verdict("t::b|c", "failed", True),
        Verdict("t::d", "reached", True, "src/d.py"),
        Verdict("t::e", "machine", True),
        Verdict("t::g", "crashed", True),
    ]
    text = ex.summary(verdicts, {"t::f": False}, {"t::a": 41, "t::f": 5}, "https://g/o/r/issues/")
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert rows == [
        "| `t::a` | failed, then passed; this change does not reach it"
        " | [#41](https://g/o/r/issues/41) |",
        "| `t::b\\|c` | failed, then failed again on retry | |",
        "| `t::d` | failed, then passed; this change reaches it through src/d.py | |",
        "| `t::e` | failed, then passed; red: this looks like the machine, not the code | |",
        "| `t::g` | killed its process: a fault in the code or the machine, not a flake | |",
        "| `t::f` | flagged, passed | [#5](https://g/o/r/issues/5) |",
    ]
    assert "| test | verdict | issue |" in text.splitlines()


def test_on_main_the_summary_says_what_main_prints():
    verdicts = [Verdict("t::a", "flaky", False), Verdict("t::d", "reached", True, "src/d.py")]
    text = ex.summary(verdicts, {}, {}, "u/", since="0123456789")
    forgiven = "failed, then passed; no commit since the last green run reaches it"
    assert f"| `t::a` | {forgiven} | |" in text
    assert (
        "| `t::d` | failed, then passed; the commits since 0123456 reach it through src/d.py | |"
        in text
    )


def test_every_run_in_ci_appends_the_table_and_the_flag_lines(monkeypatch, tmp_path):
    FakeIssues(monkeypatch, {"t::f": issues.Issue(5, True)})
    in_ci(monkeypatch, "refs/pull/7/merge")
    summary = tmp_path / "summary.md"
    summary.write_text("before\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    f = flagger(tmp_path)
    first = [*events(["b"], ["t::a"]), *flagged_outcome("t::f", "failed", 5)]
    run_flagged(f, FakeAdapter(script=[(1, first), (0, events(["t::a"]))]))
    text = summary.read_text()
    assert text.startswith("before\n")
    assert (
        "| `t::a` | failed, then passed; this change does not reach it"
        " | [#41](https://github.com/o/r/issues/41) |" in text
    )
    assert "| `t::f` | flagged, failed | [#5](https://github.com/o/r/issues/5) |" in text
    assert "- t::a: flagged as flaky: #41 (new)" in text


def test_no_summary_without_the_variable_or_anything_to_say(monkeypatch, tmp_path):
    FakeIssues(monkeypatch)
    in_ci(monkeypatch, "refs/pull/7/merge")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    run_flagged(flagger(tmp_path), FakeAdapter(script=[(0, events(["b"]))]))
    assert not summary.exists()
