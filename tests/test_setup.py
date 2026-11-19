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
