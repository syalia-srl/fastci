"""The map file: version 3's fields round-trip, and older maps still load."""

import gzip
import json

from fastci.python.mapfile import VERSION, FileEntry, TestMap

NEW = ("unattributed", "executable", "phases", "fixtures", "requested", "fixture_runs", "spawns")


def small(**extra):
    return TestMap(
        sha="abc",
        tests=["t.py::test_x[a[1]]", "t.py::test_y"],
        durations=[0.5, 0.25],
        always={1},
        files={"src/m.py": FileEntry(tests={0}, funcs={"f": (1, 3, {0})}, lines={2: ("h", {0})})},
        reads={"data.json": {1}},
        **extra,
    )


def test_version_3_round_trips_every_new_field(tmp_path):
    tm = small(
        unattributed={"src/m.py": {1}},
        executable={"src/m.py": {1, 2, 3}, "src/dead.py": {1}},
        phases=[(0.1, 0.3, 0.1), (0.0, 0.25, 0.0)],
        fixtures=[("db", "module", "tests")],
        requested=[[0], []],
        fixture_runs=[(0, 0, "gw0", 0.1)],
        spawns={1},
        lane={
            "args": ["-m", "not slow"],
            "deselected": ["t.py::test_z"],
            "skipped": {"t.py::test_w": "no db"},
        },
    )
    tm.save(str(tmp_path / "m.json.gz"))
    back = TestMap.load(str(tmp_path / "m.json.gz"))
    assert VERSION == 3 and back.version == 3
    for name in ("tests", *NEW, "lane"):
        assert getattr(back, name) == getattr(tm, name), name


def test_a_version_2_map_loads_with_empty_new_fields(tmp_path):
    p = tmp_path / "old.json.gz"
    small().save(str(p))
    data = json.loads(gzip.open(p, "rt").read())
    data["version"] = 2
    for k in (*NEW, "lane"):
        data.pop(k, None)
    with gzip.open(p, "wt") as f:
        json.dump(data, f)
    back = TestMap.load(str(p))
    assert back.version == 2 and back.phases == [] and back.executable == {} and back.lane == {}
