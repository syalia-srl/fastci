import pytest

from fastci.config import FlakyConfig
from fastci.flaky import Reach, judge, reach_of, strip_xdist
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
