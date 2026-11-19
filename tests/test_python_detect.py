import sys
from pathlib import Path

from fastci.python.adapter import PythonAdapter

FIX = Path(__file__).parent / "fixtures"


def test_full_project_checks_in_order():
    [p] = PythonAdapter().detect(str(FIX / "full"))
    assert p.adapter == "python" and p.root == "."
    assert {"uv", "pytest", "xdist", "ruff", "ty"} <= p.tools
    assert [c.name for c in p.checks] == ["lock", "format", "lint", "types"]
    assert p.checks[3].run == "uv run ty check"
    assert p.checks[1].fix == "uv run ruff format"


def test_bare_project_has_no_checks():
    [p] = PythonAdapter().detect(str(FIX / "bare"))
    assert p.checks == () and "pytest" not in p.tools


def test_no_python_project(tmp_path):
    assert PythonAdapter().detect(str(tmp_path)) == []


def test_tests_lists_ids_with_marks(tmp_path):
    full = FIX / "full"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text((full / "tests" / "test_x.py").read_text())
    (tmp_path / "pyproject.toml").write_text((full / "pyproject.toml").read_text())
    [p] = PythonAdapter(python=[sys.executable]).detect(str(tmp_path))
    got = dict(PythonAdapter(python=[sys.executable]).tests(p, str(tmp_path)))
    assert got == {
        "tests/test_x.py::test_passes": frozenset(),
        "tests/test_x.py::test_on_gpu": frozenset({"gpu"}),
    }


def test_marks_are_registered(pytester):
    pytester.makepyfile("import pytest\n@pytest.mark.gpu\ndef test_a(): pass\n")
    assert pytester.runpytest("--strict-markers").ret == 0


def test_selected_tests_run_in_the_select_order(pytester, monkeypatch):
    pytester.makepyfile(test_a="def test_1(): pass\ndef test_2(): pass\n")
    sel = pytester.path / "sel.txt"
    sel.write_text("test_a.py::test_2\ntest_a.py::test_1\n")
    monkeypatch.setenv("FASTCI_SELECT", str(sel))
    r = pytester.runpytest_subprocess("-v", "-p", "fastci.python.plugin")
    out = r.stdout.str()
    assert out.index("test_2 PASSED") < out.index("test_1 PASSED")
