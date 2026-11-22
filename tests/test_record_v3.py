"""What map version 3 records: phases, fixture runs, child processes, the lane."""

import glob
import importlib.util
import json

import pytest

CASE = """
import subprocess, sys, time
import pytest

@pytest.fixture(scope="module")
def slow_db():
    time.sleep(0.2)
    return 1

@pytest.mark.parametrize("n", [1, 2])
def test_uses_db(slow_db, n):
    time.sleep(0.05)

def test_spawns():
    subprocess.run([sys.executable, "-c", "pass"], check=True)

@pytest.mark.skip(reason="no db here")
def test_skipped():
    pass

@pytest.mark.slow
def test_deselected():
    pass
"""


@pytest.fixture
def recorded(pytester, tmp_path, monkeypatch):
    """Run pytest with the plugin recording; return every tests-*.json merged."""

    def run(files: dict, *args):
        work = tmp_path / "record"
        monkeypatch.setenv("FASTCI_RECORD", str(work))
        monkeypatch.setenv("FASTCI_ROOT", str(pytester.path))
        pytester.makepyfile(**files)
        pytester.makeini("[pytest]\nmarkers =\n    slow: slow\n")
        pytester.runpytest_subprocess("-p", "fastci.python.plugin", *args)
        merged: dict = {}
        for f in sorted(glob.glob(str(work / "tests-*.json"))):
            for k, v in json.loads(open(f).read()).items():
                if isinstance(v, dict):
                    merged.setdefault(k, {}).update(v)
                elif isinstance(v, list):
                    merged.setdefault(k, []).extend(v)
        return merged

    return run


def test_the_plugin_records_phases_fixture_runs_spawns_and_the_lane(recorded):
    notes = recorded({"test_case": CASE}, "-m", "not slow")
    ids = {k.split("::")[-1]: k for k in notes["phases"]}
    setup, call, _ = notes["phases"][ids["test_uses_db[1]"]]
    assert setup >= 0.2 and call >= 0.05
    assert notes["phases"][ids["test_uses_db[2]"]][0] < 0.1  # the module fixture is reused
    runs = [r for r in notes["fixture_runs"] if r[0] == "slow_db"]
    assert len(runs) == 1
    assert runs[0][1] == "module" and runs[0][4] == "main" and runs[0][5] >= 0.2
    assert runs[0][3] == ids["test_uses_db[1]"]
    assert any(r[:2] == ["slow_db", "module"] for r in notes["requested"][ids["test_uses_db[1]"]])
    assert notes["spawns"] == [ids["test_spawns"]]
    assert [d.split("::")[-1] for d in notes["deselected"]] == ["test_deselected"]
    assert list(notes["skipped"].values()) == ["no db here"]
    assert notes["args"][-2:] == ["-m", "not slow"]


@pytest.mark.skipif(importlib.util.find_spec("xdist") is None, reason="needs pytest-xdist")
def test_under_xdist_a_module_fixture_runs_once_per_worker_and_module(recorded):
    mod = """
import pytest
@pytest.fixture(scope="module")
def db():
    return 1
@pytest.mark.parametrize("n", range(6))
def test_x(db, n):
    pass
"""
    notes = recorded({"test_one": mod, "test_two": mod}, "-n", "2")
    runs = [r for r in notes["fixture_runs"] if r[0] == "db"]
    assert runs and all(r[4].startswith("gw") for r in runs)
    keys = [(r[4], r[3].split("::")[0]) for r in runs]
    assert len(keys) == len(set(keys))
