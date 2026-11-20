import json

import pytest

from fastci.config import FlakyConfig
from fastci.flaky import History, Reach, judge, reach_of, strip_xdist
from fastci.python.pick import Pick


def pick(reach, files=(), full=False):
    return Pick(full, set(reach), set(files), {t: "src/app.py:f" for t in reach}, reach=reach)


def test_shared_startup_code_does_not_reach():
    r = reach_of(pick({"t.py::a": 0.05, "t.py::b": 0.9}), set(), 0.2)
    assert "t.py::a" in r and "t.py::b" not in r
    assert r.why["t.py::a"] == "src/app.py:f"


def test_changed_test_file_reaches_all_its_tests():
    r = reach_of(pick({}, files={"tests/test_x.py"}), set(), 0.2)
    assert "tests/test_x.py::test_y[1]" in r


def test_full_pick_reaches_every_test_through_the_path_that_changed():
    p = Pick(True, {"tests/test_z.py::t"}, set(), {"*": "uv.lock changed"})
    r = reach_of(p, set(), 0.2)
    assert r.tests is None and "tests/test_z.py::t" in r
    [v] = judge(["tests/test_z.py::t"], {"tests/test_z.py::t"}, set(), r, FlakyConfig())
    assert (v.kind, v.blocks, v.why) == ("reached", True, "uv.lock")


def test_no_pick_reaches_everything():
    assert "anything::t" in reach_of(None, set(), 0.2)


def test_files_listed_in_always_do_not_reach():
    p = Pick(False, set(), {"tests/test_always.py"}, {"tests/test_always.py": "listed in always"})
    assert "tests/test_always.py::t" not in reach_of(p, set(), 0.2)
    assert "tests/test_always.py::t" in reach_of(p, {"tests/test_always.py"}, 0.2)


def test_a_test_the_map_does_not_know_is_reached():
    r = reach_of(
        pick({"t.py::a": 0.05, "t.py::b": 0.9}), set(), 0.2, frozenset({"t.py::a", "t.py::b"})
    )
    assert "t.py::b" not in r and "t.py::new" in r
    [v] = judge(["t.py::new"], {"t.py::new"}, set(), r, FlakyConfig())
    assert (v.kind, v.blocks, v.why) == ("reached", True, "the map does not know this test")


NONE = Reach(frozenset())
CFG = FlakyConfig()


def kinds(vs):
    return [(v.id, v.kind, v.blocks) for v in vs]


def test_crash_is_never_forgiven():
    assert kinds(judge(["a"], {"a"}, {"a"}, NONE, CFG)) == [("a", "crashed", True)]


def test_failed_again_is_red():
    assert kinds(judge(["a"], set(), set(), NONE, CFG)) == [("a", "failed", True)]


def test_three_passing_on_retry_is_the_machine():
    vs = judge(["a", "b", "c"], {"a", "b", "c"}, set(), NONE, CFG)
    assert {v.kind for v in vs} == {"machine"} and all(v.blocks for v in vs)


def test_two_passing_on_retry_are_flakes():
    assert {v.kind for v in judge(["a", "b"], {"a", "b"}, set(), NONE, CFG)} == {"flaky"}


def test_crashed_do_not_count_towards_the_machine():
    vs = judge(["a", "b", "c"], {"a", "b", "c"}, {"c"}, NONE, CFG)
    assert kinds(vs) == [("a", "flaky", False), ("b", "flaky", False), ("c", "crashed", True)]


def test_reached_stays_red_with_its_reason():
    r = Reach(frozenset({"a"}), why={"a": "src/up.py:resume"})
    [v] = judge(["a"], {"a"}, set(), r, CFG)
    assert (v.kind, v.blocks, v.why) == ("reached", True, "src/up.py:resume")


def test_reached_through_its_test_file():
    r = Reach(frozenset(), frozenset({"tests/test_x.py"}))
    [v] = judge(["tests/test_x.py::t"], {"tests/test_x.py::t"}, set(), r, CFG)
    assert (v.kind, v.why) == ("reached", "tests/test_x.py")


def test_forgive_reached():
    r = Reach(frozenset({"a"}))
    [v] = judge(["a"], {"a"}, set(), r, FlakyConfig(forgive_reached=True))
    assert (v.kind, v.blocks) == ("flaky", False)


def test_everything_reached_when_unjudgeable():
    [v] = judge(["a"], {"a"}, set(), Reach.everything(), CFG)
    assert v.kind == "reached" and v.why == "fastci cannot tell what this change reaches"


@pytest.mark.parametrize(
    "args,out",
    [
        (["-q", "-n", "auto", "-x"], ["-q", "-x"]),
        (["-n4", "--dist", "loadfile"], []),
        (["--numprocesses=2", "--dist=load", "-k", "x"], ["-k", "x"]),
    ],
)
def test_strip_xdist(args, out):
    assert strip_xdist(args) == out


def _where(n: int) -> dict:
    return {"sha": f"s{n}", "run": n, "date": f"2026-11-{n % 28 + 1:02d}", "error": f"e{n}"}


def test_history_keeps_the_last_50_outcomes_and_the_first_and_last_failure():
    h = History()
    for n in range(60):
        e = h.record("t.py::test_a", n % 3 == 0, _where(n))
    assert len(e.main) == 50
    assert e.main == [int(n % 3 == 0) for n in range(10, 60)]
    assert e.first == _where(0)
    assert e.last_failure == _where(57)
    assert h.rate("t.py::test_a") == (sum(e.main), 50)
    assert h.rate("t.py::other") == (0, 0)


def test_history_first_failure_waits_for_a_failure():
    h = History()
    e = h.record("t.py::test_a", False, _where(1))
    assert (e.first, e.last_failure) == (None, None)
    e = h.record("t.py::test_a", True, _where(2))
    assert e.first == e.last_failure == _where(2)


@pytest.mark.parametrize("content", ["{not json", '{"version": 1, "tests": 3}', "[]"])
def test_history_unreadable_file_loads_empty(tmp_path, content):
    p = tmp_path / "flaky.json"
    p.write_text(content)
    assert History.load(p).tests == {}
    assert History.load(tmp_path / "missing.json").tests == {}


def test_history_keeps_the_entries_it_can_read_and_ignores_unknown_keys(tmp_path):
    p = tmp_path / "flaky.json"
    tests = {
        "t.py::test_new": {"main": [1, 0], "added_later": True},
        "t.py::test_null": {"main": None},
        "t.py::test_text": "x",
        "t.py::test_ok": {"main": [0], "issue": 9},
    }
    p.write_text(json.dumps({"version": 2, "tests": tests}))
    h = History.read(p)
    assert set(h.tests) == {"t.py::test_new", "t.py::test_ok"}
    assert h.tests["t.py::test_new"].main == [1, 0] and h.tests["t.py::test_ok"].issue == 9
    assert History.load(p) == h


@pytest.mark.parametrize(
    "bad",
    [
        {"clean": "3"},
        {"clean": True},
        {"clean": 2.0},
        {"landed": 1},
        {"landed_sha": 7},
        {"main": [0, True]},
        {"main": [0, 1.0]},
        {"main": "01"},
        {"issue": "18"},
        {"issue": True},
        {"first": "abc"},
        {"last_failure": []},
    ],
)
def test_history_drops_an_entry_with_a_field_of_the_wrong_type(tmp_path, bad):
    # A hand-edited artifact must not crash the run after its stages and lose the summary.
    p = tmp_path / "flaky.json"
    good = {"main": [0, 1], "clean": 2, "landed": True, "landed_sha": "abc", "issue": 4}
    tests = {"t.py::bad": {**good, **bad}, "t.py::good": good}
    p.write_text(json.dumps({"version": 1, "tests": tests}))
    assert set(History.read(p).tests) == {"t.py::good"}


@pytest.mark.parametrize("content", ["{not json", '{"version": 1, "tests": 3}', "[]"])
def test_history_read_refuses_a_file_that_is_not_one(tmp_path, content):
    p = tmp_path / "flaky.json"
    p.write_text(content)
    with pytest.raises(ValueError):
        History.read(p)


def test_history_round_trips_node_ids(tmp_path):
    ids = ["t.py::test_x[svc-api]", "t.py::test_y[a b/c]", "t.py::test_z[ñandú-日本]"]
    h = History()
    for i, t in enumerate(ids):
        h.record(t, True, _where(i)).issue = 30 + i
    p = tmp_path / ".fastci" / "flaky.json"
    h.save(p)
    data = json.loads(p.read_text())
    assert data["version"] == 1 and set(data["tests"]) == set(ids)
    assert History.load(p) == h


def streak(h: History, runs: int, reached_at: int | None = None, fail_at: int | None = None):
    """Whether each of ``runs`` runs on main unflags t::a: a reaching change lands at
    run ``reached_at``, and run ``fail_at`` fails."""
    return [h.unflag_due("t::a", i == reached_at, i == fail_at, 20) for i in range(1, runs + 1)]


def test_a_clean_streak_without_a_reaching_change_never_unflags():
    assert not any(streak(History(), 40))


def test_a_reaching_change_then_20_clean_runs_unflags_on_the_20th():
    assert streak(History(), 20, reached_at=1) == [False] * 19 + [True]


def test_a_failure_on_the_19th_restarts_the_count():
    due = streak(History(), 39, reached_at=1, fail_at=19)
    assert due.index(True) == 38  # 20 clean runs after the 19th, which failed


def test_a_failure_does_not_undo_the_landing():
    h = History()
    h.unflag_due("t::a", True, True, 20)
    assert h.tests["t::a"].landed and h.tests["t::a"].clean == 0


def test_unflag_after_zero_never_unflags():
    h = History()
    assert not any(h.unflag_due("t::a", True, False, 0) for _ in range(40))


def test_the_first_reaching_run_records_its_commit():
    h = History()
    h.unflag_due("t::a", False, False, 20, "s0")
    h.unflag_due("t::a", True, False, 20, "s1")
    h.unflag_due("t::a", True, False, 20, "s2")
    assert h.tests["t::a"].landed_sha == "s1"


def test_the_landing_commit_round_trips_and_old_files_load_none(tmp_path):
    p = tmp_path / "flaky.json"
    h = History()
    h.unflag_due("t::a", True, False, 20, "s1")
    h.save(p)
    assert History.load(p).tests["t::a"].landed_sha == "s1"
    p.write_text(json.dumps({"version": 1, "tests": {"t::a": {"main": [0], "landed": True}}}))
    assert History.load(p).tests["t::a"].landed_sha is None


def test_lands_only_through_a_diff_rule():
    reach = Reach(frozenset({"t::a"}), frozenset({"t2"}), known=frozenset({"t::a", "t2::b"}))
    assert reach.lands("t::a") and reach.lands("t2::b")
    assert "t::new" in reach and not reach.lands("t::new")  # unknown to the map
    assert "t::a" in Reach.everything() and not Reach.everything().lands("t::a")
