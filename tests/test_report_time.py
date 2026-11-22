"""The report's model and its time sections."""

from report_helpers import suite_of, synthetic_suite

from fastci.report import base_id, build
from fastci.report.time import by_test_file, little_code, setup_cost, slowest


def test_base_id_strips_only_the_last_parts_brackets():
    assert base_id("t.py::test_x[a[1]]") == "t.py::test_x"
    assert base_id("t.py::C::test_y[a b/c]") == "t.py::C::test_y"
    assert base_id("t.py::test_z") == "t.py::test_z"


def test_a_function_fixture_paid_by_three_tests_tops_setup_cost(health_map):
    top = setup_cost(suite_of(health_map))["entries"][0]
    assert top["fixture"] == "warm" and top["scope"] == "function"
    assert top["runs"] == 3 and top["tests"] == 3 and top["seconds"] >= 0.3 and top["rebuilt"] == 0


def test_a_module_fixture_run_twice_on_one_worker_counts_as_rebuilt():
    s = synthetic_suite(
        fixture_runs=[
            ("db", "module", "", "t.py::a", "gw0", 1.0),
            ("db", "module", "", "t.py::b", "gw0", 1.0),
            ("db", "module", "", "t.py::c", "gw1", 1.0),
        ]
    )
    assert setup_cost(s)["entries"][0]["rebuilt"] == 1


def test_parametrized_cases_are_one_slowest_entry(health_map):
    e = {x["test"]: x for x in slowest(suite_of(health_map))["entries"]}
    assert e["tests/test_param.py::test_cases"]["cases"] == 3


def test_a_test_that_waits_on_the_clock_has_much_time_little_code(health_map):
    got = [x["test"] for x in little_code(suite_of(health_map))["entries"]]
    assert got == ["tests/test_clock.py::test_waits"]


def test_the_file_floor_is_its_fastest_test_times_its_count(health_map):
    e = {x["file"]: x for x in by_test_file(suite_of(health_map))["entries"]}
    f = e["tests/test_fixture.py"]
    assert f["tests"] == 3 and f["floor"] >= 0.3


def test_tiny_and_empty_maps_build_every_section_without_error():
    for n in (0, 1, 21):
        model = build(synthetic_suite(n_tests=n), history=None)
        assert set(model) == {"version", "header", "time", "overlap", "coverage", "flaky"}


def test_durations_are_the_median_over_several_maps():
    s = synthetic_suite(durations_per_map=[[1.0], [3.0], [2.0]])
    assert s.durations == [2.0]
