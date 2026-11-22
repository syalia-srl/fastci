"""Overlap: tests that run the same code, almost the same code, or the start-up path."""

from report_helpers import suite_of, synthetic_suite

from fastci.report.overlap import near_same, same_path, startup_path


def test_two_tests_on_one_path_group_and_parametrized_cases_alone_do_not(health_map):
    groups = same_path(suite_of(health_map))["entries"]
    names = [sorted(t["test"].split("::")[-1] for t in g["tests"]) for g in groups]
    assert ["test_a", "test_b", "test_cases", "test_plain"] in names
    case = next(t for g in groups for t in g["tests"] if t["test"].endswith("test_cases"))
    assert case["cases"] == 3


def test_a_group_under_fifteen_own_lines_is_left_out(health_map):
    for g in same_path(suite_of(health_map))["entries"]:
        assert g["own_lines"] >= 15  # the three short() tests are not a group


def test_one_extra_line_makes_a_near_same_cluster(health_map):
    (c,) = near_same(suite_of(health_map))["entries"]
    assert c["file"] == "tests/test_near.py"
    adds = {t["test"].split("::")[-1]: t["adds"] for t in c["tests"]}
    assert adds == {"test_plain": 0, "test_flag": 1}


def test_functions_most_of_the_suite_runs_form_the_startup_path():
    s = synthetic_suite(
        n_tests=10,
        func_tests={"app.py::boot": range(9), "app.py::config": range(9), "x.py::f": [0]},
    )
    (e,) = startup_path(s)["entries"]
    assert sorted(e["functions"]) == ["app.py::boot", "app.py::config"] and e["tests"] == 9
