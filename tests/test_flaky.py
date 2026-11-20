from fastci.flaky import reach_of
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


def test_full_pick_reaches_only_changed_test_files():
    r = reach_of(pick({}, full=True), {"tests/test_x.py"}, 0.2)
    assert "tests/test_x.py::t" in r and "tests/test_z.py::t" not in r


def test_no_pick_reaches_everything():
    assert "anything::t" in reach_of(None, set(), 0.2)
