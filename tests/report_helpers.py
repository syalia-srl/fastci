"""Helpers shared by the report's tests: suites built in memory, and the health sample."""

import shutil

from fastci.python.mapfile import FileEntry, TestMap
from fastci.report import Suite, build


def suite_of(health_map):
    repo, tm = health_map
    return Suite(maps=[tm], root=str(repo), reach_share=0.6)  # the sample's own reach-share


def model_of(health_map):
    return build(suite_of(health_map), None)


def synthetic_suite(
    n_tests=0, durations_per_map=None, fixture_runs=None, func_tests=None, reach_share=0.2, root="."
):
    """A suite of maps built in memory. ``fixture_runs`` holds tuples (name, scope, baseid,
    test, worker, seconds); ``func_tests`` maps "file.py::func" to the test indexes that ran it."""
    per_map = durations_per_map or [[0.1] * n_tests]
    n = len(per_map[0])
    tests = [f"t.py::test_{i}" for i in range(n)]
    maps = []
    for durations in per_map:
        tm = TestMap(
            sha="s", tests=list(tests), durations=list(durations), always=set(), files={}, reads={}
        )
        tm.phases = [(0.0, d, 0.0) for d in durations]
        tm.requested = [[] for _ in tests]
        maps.append(tm)
    tm = maps[0]
    for name, scope, baseid, test, worker, seconds in fixture_runs or []:
        if test not in tm.tests:
            tm.tests.append(test)
            tm.durations.append(seconds)
            tm.phases.append((seconds, 0.0, 0.0))
            tm.requested.append([])
        key = (name, scope, baseid)
        if key not in tm.fixtures:
            tm.fixtures.append(key)
        f, t = tm.fixtures.index(key), tm.tests.index(test)
        tm.fixture_runs.append((f, t, worker, seconds))
        if f not in tm.requested[t]:
            tm.requested[t].append(f)
    for qual, ids in (func_tests or {}).items():
        path, name = qual.split("::")
        e = tm.files.setdefault(path, FileEntry())
        k = len(e.funcs) * 10 + 1
        e.funcs[name] = (k, k + 1, set(ids))
        e.lines[k + 1] = ("h", set(ids))
        e.tests |= set(ids)
    return Suite(maps=maps, root=root, reach_share=reach_share)


def model_with_test(nodeid):
    return build(synthetic_suite(durations_per_map=[[0.5]]), None) | {}


def copy_repo(repo, dest):
    out = dest / "copy"
    shutil.copytree(repo, out)
    return out
