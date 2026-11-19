import subprocess

import pytest
from conftest import run_testmap

from fastci.adapter import Check, Project
from fastci.config import Config, StageSpec
from fastci.plan import build, changed_files
from fastci.python.adapter import PY
from fastci.python.mapfile import TestMap
from fastci.python.pick import Pick, Picker
from fastci.tier import Context

NOT_RELEASE = "not (slow or gpu or browser or e2e or bench)"
CHECKS = (
    Check("lock", "uv lock --check", None, ("pyproject.toml", "uv.lock")),
    Check("format", "uv run ruff format --check", "uv run ruff format", PY),
    Check("lint", "uv run ruff check", "uv run ruff check --fix", PY),
    Check("types", "uv run ty check", None, PY),
)
PROJ = Project("python", ".", frozenset({"uv", "pytest", "ruff", "ty"}), CHECKS)
T0 = Context("T0", "origin/main", False, "laptop")
T1 = Context("T1", "origin/main", True, "pr")
T2 = Context("T2", None, True, "main")
T3 = Context("T3", None, True, "release")


def tmap(tests=("tests/test_a.py::test_a", "tests/test_b.py::test_b"), durations=(2.0, 0.5)):
    return TestMap(
        sha="abc", tests=list(tests), durations=list(durations), always=set(), files={}, reads={}
    )


def plan(ctx, changed, pick=None, tm=None, cfg=None, projects=(PROJ,), gpu=True):
    return build(
        ctx, cfg or Config(), "/repo", list(projects), changed, {".": pick}, {".": tm}, gpu
    )


def stages(steps):
    return [(s.stage, s.action) for s in steps]


def step(steps, name):
    return next(s for s in steps if s.stage == name)


def test_t1_skips_checks_when_only_docs_changed():
    steps = plan(T1, {"README.md"}, Pick(False, set(), set(), {}), tmap())
    assert stages(steps) == [
        ("lock", "skip"),
        ("format", "skip"),
        ("lint", "skip"),
        ("types", "skip"),
        ("always", "skip"),
        ("tests", "skip"),
    ]
    assert step(steps, "format").reason == "no changed file matches"


def test_t0_without_map_runs_only_changed_test_files():
    steps = plan(T0, {"tests/test_x.py", "src/a.py"})
    t = step(steps, "tests")
    assert t.action == "run" and t.select == ["tests/test_x.py"]
    assert t.reason == "no map; only changed test files, main runs the rest"
    assert step(steps, "always").action == "run"


def test_t1_without_map_runs_every_test():
    t = step(plan(T1, {"src/a.py"}), "tests")
    assert t.action == "run" and t.select is None and t.reason == "no map; every test runs"


def test_no_merge_base_runs_everything():
    steps = plan(T1, None, None, tmap())
    assert all(s.action == "run" for s in steps if s.stage in ("format", "lint", "types"))
    t = step(steps, "tests")
    assert t.select is None and t.reason.startswith("no merge-base with origin/main")


def test_picked_tests_run_fastest_first():
    pick = Pick(False, {"tests/test_a.py::test_a", "tests/test_b.py::test_b"}, set(), {})
    t = step(plan(T1, {"src/a.py"}, pick, tmap()), "tests")
    assert t.select == ["tests/test_b.py::test_b", "tests/test_a.py::test_a"]
    assert t.command[-2:] == ["-m", NOT_RELEASE]


def test_t2_runs_whole_fast_suite_and_no_release_stage():
    steps = plan(T2, None, None, tmap())
    t = step(steps, "tests")
    assert t.select is None and t.command[-2:] == ["-m", NOT_RELEASE]
    assert "release" not in [s.stage for s in steps]


def test_t3_release_stage_drops_gpu_without_gpu():
    r = step(plan(T3, None, None, tmap(), gpu=False), "release")
    assert r.command[-2:] == ["-m", "slow or browser or e2e or bench"]
    assert "gpu" in r.reason


def test_bare_project_has_no_steps():
    bare = Project("python", ".", frozenset(), ())
    assert plan(T1, {"src/a.py"}, projects=(bare,)) == []


def test_config_stage_after_lint_and_override():
    cfg = Config(stages={"docs": StageSpec("make docs", after="lint"), "types": StageSpec("")})
    steps = plan(T2, None, None, tmap(), cfg=cfg)
    assert [s.stage for s in steps][:4] == ["lock", "format", "lint", "docs"]
    assert "types" not in [s.stage for s in steps]


def test_config_stage_outside_its_tiers_is_left_out():
    cfg = Config(stages={"docs": StageSpec("make docs", tiers=frozenset({"T2"}))})
    assert "docs" not in [s.stage for s in plan(T1, {"src/a.py"}, cfg=cfg)]


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def tmp_git_repo(tmp_path):
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "start")
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "fetch", "-q", "origin")
    return repo


def test_changed_files_sees_untracked_test(tmp_git_repo):
    (tmp_git_repo / "tests" / "test_new.py").write_text("def test_n(): pass\n")
    assert "tests/test_new.py" in changed_files(str(tmp_git_repo), "origin/main")


def test_untracked_test_file_is_picked_with_a_map():
    pick = Pick(False, set(), set(), {})
    t = step(plan(T0, {"tests/test_new.py"}, pick, tmap()), "tests")
    assert t.select == ["tests/test_new.py"]


def test_changed_files_none_without_base(tmp_git_repo):
    assert changed_files(str(tmp_git_repo), "origin/nope") is None


def test_pick_handles_file_absent_from_map(sample):
    code, out, err = run_testmap(sample, "record", "--", "-q", "-p", "no:cacheprovider")
    assert code == 0, err
    (sample / "src" / "sample" / "new.py").write_text("def f():\n    return 1\n")
    git(sample, "add", ".")
    tm = TestMap.load(str(sample / ".fastci" / "map.json.gz"))
    p = Picker(tm, str(sample), Config().tests).pick("HEAD")
    assert p.full is False


def test_plan_command_prints_steps(tmp_git_repo, monkeypatch, capsys):
    from fastci import cli

    (tmp_git_repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n[dependency-groups]\ndev = ["pytest", "ruff"]\n'
    )
    monkeypatch.chdir(tmp_git_repo)
    monkeypatch.delenv("CI", raising=False)
    assert cli.main(["plan"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("no CI variable")
    assert out[1].startswith("1. format") and "pyproject.toml" not in out[1]
    assert any(line.startswith("4. tests") and "no map" in line for line in out)
