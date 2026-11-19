import gzip
import json
import urllib.error

from fastci import execute as ex
from fastci import fetch
from fastci.adapter import Project
from fastci.plan import Step
from fastci.python.mapfile import TestMap
from fastci.tier import Context

PROJ = Project("fake", ".", frozenset({"pytest"}), ())


class FakeAdapter:
    name = "fake"

    def __init__(self, code=0):
        self.code, self.calls = code, []

    def run_tests(self, project, repo, select, args, env, wrap=lambda a: a):
        self.calls.append((select, args, wrap(["pytest"])))
        return self.code


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


T1 = Context("T1", "origin/main", True, "pr")


def test_ensure_map_skips_download_when_sha_matches(monkeypatch, tmp_path):
    path = tmp_path / ".fastci" / "map.json.gz"
    path.parent.mkdir()
    save_map(path, "abc")
    monkeypatch.setenv("GH_TOKEN", "t")
    art = {"workflow_run": {"head_sha": "abc"}, "archive_download_url": "u"}
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: art)
    monkeypatch.setattr(fetch, "_download", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert ex.ensure_map(str(tmp_path), T1, "o/r", "main") == str(path)


def test_ensure_map_downloads_a_newer_map(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "t")
    art = {"workflow_run": {"head_sha": "new"}, "archive_download_url": "u"}
    monkeypatch.setattr(fetch, "newest_artifact", lambda *a, **k: art)
    monkeypatch.setattr(fetch, "_download", lambda url, token, out: save_map(out, "new"))
    path = ex.ensure_map(str(tmp_path), T1, "o/r", "main")
    with gzip.open(path) as f:
        assert json.load(f)["sha"] == "new"


def test_ensure_map_returns_none_offline(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GH_TOKEN", "t")

    def boom(*a, **k):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(fetch, "newest_artifact", boom)
    assert ex.ensure_map(str(tmp_path), T1, "o/r", "main") is None
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
    ex.ensure_map(str(tmp_path), Context("T0", "origin/main", False, "laptop"), "o/r", "main")
    ex.ensure_map(str(tmp_path), T1, "o/r", "main")
    assert seen[0] == 5 and seen[-1] == 60
