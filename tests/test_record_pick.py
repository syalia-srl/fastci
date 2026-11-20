"""Record the sample project's map, change one thing, and check what is picked."""

import re

import pytest
from conftest import commit, run_testmap


@pytest.fixture
def mapped(sample):
    code, out, err = run_testmap(sample, "record", "--", "-q", "-p", "no:cacheprovider")
    assert code == 0, err + out
    return sample


def picked(repo, *extra):
    code, out, err = run_testmap(repo, "pick", "--base", "HEAD~1", "--head", "HEAD", *extra)
    assert code == 0, err
    return set(out.split())


def edit(repo, path, old, new):
    p = repo / path
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new))
    commit(repo)


def test_a_changed_line_picks_only_the_tests_that_ran_it(mapped):
    edit(mapped, "src/sample/calc.py", "return math.sqrt(x)", "return math.sqrt(x) + 0")
    assert picked(mapped) == {
        "tests/test_calc.py::test_root",
        "tests/test_coherence.py::test_always_runs",
    }


def test_a_function_other_functions_call_picks_their_tests_too(mapped):
    edit(mapped, "src/sample/calc.py", "return a + b", "return b + a")
    got = picked(mapped)
    assert {"tests/test_calc.py::test_add", "tests/test_calc.py::test_mul"} <= got
    assert "tests/test_calc.py::test_root" not in got


def test_code_run_only_in_a_child_process_is_mapped_to_its_test(mapped):
    edit(mapped, "src/sample/server.py", 'return "served"', 'return "served" if True else ""')
    assert "tests/test_sub.py::test_child_serves" in picked(mapped)


def test_a_file_a_test_reads_picks_that_test(mapped):
    edit(mapped, "static/site.css", "red", "red ")
    got = picked(mapped)
    assert "tests/test_css.py::test_css_is_red" in got
    assert "tests/test_calc.py::test_add" not in got


def test_a_comment_or_docstring_picks_nothing_but_always(mapped):
    edit(
        mapped,
        "src/sample/calc.py",
        '"""Arithmetic the tests use."""',
        '"""Arithmetic, used by tests."""',
    )
    assert picked(mapped) == {"tests/test_coherence.py::test_always_runs"}


def test_an_import_picks_one_test_that_loads_the_module(mapped):
    edit(mapped, "src/sample/calc.py", "import math\n", "import math\nimport os\n")
    got = picked(mapped) - {"tests/test_coherence.py::test_always_runs"}
    assert len(got) == 1 and next(iter(got)).startswith("tests/test_calc.py::")


def test_a_changed_test_file_runs_whole(mapped):
    (mapped / "tests" / "test_css.py").write_text(
        (mapped / "tests" / "test_css.py").read_text() + "\n# touched\n"
    )
    commit(mapped)
    assert "tests/test_css.py" in picked(mapped)


def test_the_lockfile_or_pyproject_runs_everything(mapped):
    edit(mapped, "pyproject.toml", 'testpaths = ["tests"]', 'testpaths = ["tests"]\n')
    assert picked(mapped) == {"ALL"}


def test_why_names_the_reason(mapped):
    edit(mapped, "static/site.css", "red", "red ")
    _, out, _ = run_testmap(mapped, "pick", "--base", "HEAD~1", "--head", "HEAD", "--why")
    assert re.search(r"test_css_is_red\tstatic/site.css read", out)


def test_run_runs_only_the_picked_tests(mapped):
    edit(mapped, "src/sample/calc.py", "return math.sqrt(x)", "return math.sqrt(x) + 0")
    code, out, err = run_testmap(
        mapped, "run", "--base", "HEAD~1", "--", "-q", "-p", "no:cacheprovider"
    )
    assert code == 0, out + err
    assert "2 passed" in out and "deselected" in out


def test_without_a_map_every_test_runs(sample):
    _, out, err = run_testmap(sample, "pick", "--base", "HEAD")
    assert out.strip() == "ALL" and "no map" in err


def test_a_file_only_a_child_process_reads_picks_its_test(mapped):
    edit(mapped, "static/data.json", '"n": 1', '"n": 2')
    got = picked(mapped)
    assert "tests/test_sub.py::test_child_reads_data" in got
    assert "tests/test_calc.py::test_add" not in got


def test_fastci_runs_the_picked_tests_in_a_pull_request(mapped):
    import os
    import subprocess
    import sys

    edit(mapped, "src/sample/calc.py", "return a + b", "return b + a")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FASTCI_", "GITHUB_"))}
    env["CI"] = "true"
    p = subprocess.run(
        [sys.executable, "-m", "fastci.cli", "--tier", "pr", "--base", "HEAD~1"],
        cwd=mapped,
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stderr + p.stdout
    assert re.search(r"fastci: tests: \d+ picked", p.stderr), p.stderr


def test_record_with_source_uses_the_projects_interpreter(sample, monkeypatch):
    from fastci import cli
    from fastci.python import adapter, record

    seen = {}
    monkeypatch.setattr(adapter.PythonAdapter, "python", lambda self, p: ["the-projects-python"])
    monkeypatch.setattr(record, "record", lambda *a, **k: seen.update(k, sources=a[2]) or 0)
    monkeypatch.chdir(sample)
    assert cli.main(["map", "record", "--source", "src"]) == 0
    assert seen["sources"] == ["src"] and seen["python"][-1] == "the-projects-python"


def test_an_empty_map_counts_as_no_map(sample, monkeypatch, capsys):
    from fastci import cli
    from fastci.python.mapfile import TestMap

    monkeypatch.chdir(sample)
    TestMap(sha="x", tests=[], durations=[], always=set(), files={}, reads={}).save("empty.json.gz")
    assert cli.main(["map", "pick", "--base", "HEAD", "--map", "empty.json.gz"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "ALL" and "has no tests" in out.err


def test_version(capsys):
    import fastci
    from fastci import cli

    with __import__("pytest").raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == f"fastci {fastci.__version__}"


def test_a_picked_test_carries_the_share_of_the_suite_behind_it(mapped):
    from fastci.config import TestsConfig
    from fastci.python.mapfile import TestMap
    from fastci.python.pick import Picker

    edit(mapped, "src/sample/calc.py", "return math.sqrt(x)", "return math.sqrt(x) + 0")
    tm = TestMap.load(str(mapped / ".fastci" / "map.json.gz"))
    p = Picker(tm, str(mapped), TestsConfig()).pick("HEAD~1", "HEAD")
    assert p.reach == {"tests/test_calc.py::test_root": 1 / len(tm.tests)}
    assert "tests/test_coherence.py::test_always_runs" in p.tests
