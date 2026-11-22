"""Coverage by file, files never imported, what the map cannot see, and flaky tests."""

from report_helpers import copy_repo, suite_of

from fastci.flaky import History
from fastci.report import Suite
from fastci.report.coverage import by_file, flaky_section, function_bodies, header, never_imported


def test_lines_run_at_import_count_as_covered(health_map):
    e = {x["file"]: x for x in by_file(suite_of(health_map))["entries"]}
    assert e["src/health/early.py"]["percent"] == 100.0


def test_untested_functions_need_a_body_line_and_nest_under_entered_parents(health_map):
    e = {x["file"]: x for x in by_file(suite_of(health_map))["entries"]}
    names = [u["function"] for u in e["src/health/untested.py"]["untested"]]
    assert names == ["never_called", "outer"]  # inner is folded: outer was never entered


def test_a_file_a_spawning_test_reads_is_tagged_spawns(health_map):
    e = {x["file"]: x for x in by_file(suite_of(health_map))["entries"]}
    assert "spawns" in e["src/health/core.py"]["tags"]


def test_dead_code_scripts_and_lazy_imports_are_told_apart(health_map):
    e = {x["file"]: x["kind"] for x in never_imported(suite_of(health_map))["entries"]}
    assert e == {
        "src/health/dead.py": "module",
        "src/health/tool.py": "script",
        "src/health/__main__.py": "script",
        "src/health/lazy.py": "imported lazily",
    }


def test_the_header_names_what_the_map_cannot_see(health_map):
    h = header(suite_of(health_map))
    assert h["deselected"] == 1 and h["spawns"] == 1
    assert h["skipped"] == [
        {"test": "tests/test_lane.py::test_skip", "reason": "needs a gpu", "every_map": True}
    ]
    assert h["unparsed"] == ["src/health/broken.py"]
    assert h["head_differs"] is False and h["date"]


def test_a_file_changed_since_the_map_is_skipped_with_a_note(health_map, tmp_path):
    repo, tm = health_map
    moved = copy_repo(repo, tmp_path)
    (moved / "src/health/untested.py").write_text("x = 1\n")
    s = Suite(maps=[tm], root=str(moved), reach_share=0.6)
    e = {x["file"]: x for x in by_file(s)["entries"]}
    assert e["src/health/untested.py"]["untested"] is None
    assert any("src/health/untested.py" in n for n in header(s)["notes"])


def test_source_that_does_not_parse_has_no_functions():
    assert function_bodies('print "x"\n') == []
    assert function_bodies(b"\xff\xfe".decode("latin-1")) == []


def test_flaky_rows_come_from_the_history():
    h = History()
    for failed in (1, 0, 0, 1):
        h.record("t.py::test_x", bool(failed), {"sha": "s"})
    (row,) = flaky_section(h)["entries"]
    assert row["failures"] == 2 and row["runs"] == 4 and row["rate"] == 0.5
    assert flaky_section(None)["note"].startswith("no flaky history")


def test_a_file_a_deselected_tests_file_imports_is_tagged_outside_the_lane(tmp_path):
    from fastci.python.mapfile import TestMap

    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/mod.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_gpu.py").write_text(
        "from pkg.mod import x\n\ndef test_gpu():\n    pass\n"
    )
    tm = TestMap(sha="", tests=[], durations=[], always=set(), files={}, reads={})
    tm.executable = {"src/pkg/mod.py": {1}}
    tm.lane = {"deselected": ["tests/test_gpu.py::test_gpu"], "skipped": {}}
    (e,) = by_file(Suite(maps=[tm], root=str(tmp_path), reach_share=0.2))["entries"]
    assert e["tags"] == ["outside the lane"]


def test_the_header_counts_the_workers(health_map):
    assert header(suite_of(health_map))["workers"] == 1
