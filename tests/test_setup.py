import shutil
from pathlib import Path

import fastci
from fastci import setup
from fastci.python.mapfile import TestMap

FIX = Path(__file__).parent / "fixtures"


def copy(tmp_path, name):
    dst = tmp_path / name
    shutil.copytree(FIX / name, dst)
    return dst


def by_kind(changes, kind):
    return [c for c in changes if c.kind == kind]


def test_bare_proposes_workflow_and_gitignore_only(tmp_path):
    changes = setup.propose(str(copy(tmp_path, "setup-bare")))
    assert {c.kind for c in changes} == {"add-file", "append-line"}
    assert {c.path for c in changes} == {".github/workflows/fastci.yml", ".gitignore"}


def test_full_proposes_browser_mark_and_replacement(tmp_path):
    changes = setup.propose(str(copy(tmp_path, "setup-full")))
    [mark] = by_kind(changes, "mark")
    assert mark.path == "tests/test_ui.py"
    assert "pytestmark = pytest.mark.browser" in mark.after and "import pytest" in mark.after
    [wf] = by_kind(changes, "replace-workflow")
    assert wf.path == ".github/workflows/ci.yml" and "make docs" in wf.reason
    assert "make docs" in wf.after and "pytest" not in wf.after


def test_workflow_pins_the_installed_version(tmp_path):
    [wf] = [c for c in setup.propose(str(copy(tmp_path, "setup-bare"))) if c.kind == "add-file"]
    assert f"uvx fastci@{fastci.__version__}" in wf.after
    assert "fetch-depth: 0" in wf.after and "name: fastci" in wf.after


def test_write_twice_changes_nothing(tmp_path):
    root = str(copy(tmp_path, "setup-full"))
    setup.apply(root, setup.propose(root))
    assert setup.propose(root) == []


def test_from_map_marks_slow_tests(tmp_path):
    root = copy(tmp_path, "setup-full")
    tm = TestMap(
        sha="a",
        tests=["tests/test_x.py::test_x", "tests/test_x.py::test_slow_one"],
        durations=[0.1, 12.0],
        always=set(),
        files={},
        reads={},
    )
    tm.save(str(tmp_path / "m.json.gz"))
    marks = [
        c
        for c in setup.propose(str(root), str(tmp_path / "m.json.gz"))
        if c.kind == "mark" and c.path == "tests/test_x.py"
    ]
    assert len(marks) == 1
    assert "@pytest.mark.slow\ndef test_slow_one" in marks[0].after
    assert "@pytest.mark.slow\ndef test_x" not in marks[0].after


def test_diff_shows_each_change(tmp_path):
    out = setup.diff(setup.propose(str(copy(tmp_path, "setup-bare"))))
    assert "+++ b/.github/workflows/fastci.yml" in out and "+.fastci/" in out


def test_setup_command_prints_then_writes(tmp_path, monkeypatch, capsys):
    from fastci import cli

    root = copy(tmp_path, "setup-bare")
    monkeypatch.chdir(root)
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "python · pytest"
    assert "would add  .github/workflows/fastci.yml" in out
    assert not (root / ".github").exists()
    assert cli.main(["setup", "--write"]) == 0
    assert (root / ".github" / "workflows" / "fastci.yml").exists()


def test_check_lists_uncovered_steps(tmp_path):
    [gap] = setup.check(str(copy(tmp_path, "setup-full")))
    assert gap.workflow == ".github/workflows/ci.yml" and gap.job == "test"
    assert gap.step == "docs" and gap.run == "make docs"


def test_check_respects_configured_stage(tmp_path):
    root = copy(tmp_path, "setup-full")
    pp = root / "pyproject.toml"
    pp.write_text(pp.read_text() + '\n[tool.fastci.stages]\ndocs = "make docs"\n')
    assert setup.check(str(root)) == []


def test_check_command_exits_1_on_gaps(tmp_path, monkeypatch, capsys):
    from fastci import cli

    monkeypatch.chdir(copy(tmp_path, "setup-full"))
    assert cli.main(["setup", "--check"]) == 1
    assert "make docs" in capsys.readouterr().out


def test_guide_prints(capsys):
    from fastci import cli

    assert cli.main(["guide"]) == 0
    out = capsys.readouterr().out
    assert "fastci setup --check" in out and "T3" in out


def test_workflows_without_pull_request_are_left_alone(tmp_path):
    root = str(copy(tmp_path, "setup-full"))
    assert all(c.path != ".github/workflows/release.yml" for c in setup.propose(root))
    assert all(g.workflow != ".github/workflows/release.yml" for g in setup.check(root))


def test_marks_skip_what_pytest_does_not_collect(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\nnorecursedirs = ["fixtures"]\n'
    )
    for d in ("tests/fixtures/p/tests", "other"):
        (tmp_path / d).mkdir(parents=True)
    ui = "from playwright.sync_api import sync_playwright\n\n\ndef test_p():\n    pass\n"
    (tmp_path / "tests/fixtures/p/tests/test_ui.py").write_text(ui)
    (tmp_path / "other/test_ui.py").write_text(ui)
    (tmp_path / "tests/test_ui.py").write_text(ui)
    marks = [c.path for c in setup.propose(str(tmp_path)) if c.kind == "mark"]
    assert marks == ["tests/test_ui.py"]
