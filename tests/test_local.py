import gzip
import json
import subprocess
import sys

import pytest

from fastci import local
from fastci.python.mapfile import TestMap

GIB = local.GIB


def test_mem_available_reads_meminfo(tmp_path):
    f = tmp_path / "meminfo"
    f.write_text("MemTotal: 16000000 kB\nMemAvailable:    8000000 kB\n")
    assert local.mem_available(str(f)) == 8000000 * 1024


def test_mem_available_unreadable(tmp_path):
    assert local.mem_available(str(tmp_path / "missing")) is None


def test_workers_bounded_by_memory_and_cpus():
    assert local.workers(16, 8 * GIB, 0.5, GIB) == 4
    assert local.workers(4, 64 * GIB, 0.5, GIB) == 3
    assert local.workers(8, GIB // 2, 0.5, GIB) == 1


def test_wrap_with_and_without_systemd():
    head = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={4 * GIB}"]
    assert local.wrap(["pytest"], 8 * GIB, 0.5, True)[:6] == head
    assert local.wrap(["pytest"], 8 * GIB, 0.5, True)[-4:] == ["nice", "-n", "10", "pytest"]
    assert local.wrap(["pytest"], None, 0.5, True) == ["nice", "-n", "10", "pytest"]
    assert local.wrap(["pytest"], 8 * GIB, 0.5, False) == ["nice", "-n", "10", "pytest"]


def test_v1_map_loads_with_zero_peak(tmp_path):
    path = tmp_path / "m.json.gz"
    TestMap(sha="a", tests=[], durations=[], always=set(), files={}, reads={}).save(str(path))
    with gzip.open(path, "rt") as f:
        data = json.load(f)
    data["version"] = 1
    data.pop("peak_rss", None)
    with gzip.open(path, "wt") as f:
        json.dump(data, f)
    assert TestMap.load(str(path)).peak_rss == 0


def test_peak_rss_round_trips(tmp_path):
    path = tmp_path / "m.json.gz"
    tm = TestMap(sha="a", tests=[], durations=[], always=set(), files={}, reads={})
    tm.peak_rss = 123
    tm.save(str(path))
    assert TestMap.load(str(path)).peak_rss == 123


def test_killed_by_cap():
    assert local.killed_by_cap(137) and local.killed_by_cap(-9)
    assert not local.killed_by_cap(1)


@pytest.mark.skipif(not local.has_systemd_user(), reason="needs a systemd user session")
def test_cap_kills_a_hog():
    hog = [sys.executable, "-c", "x = bytearray(512 * 1024 * 1024); import time; time.sleep(1)"]
    code = subprocess.run(local.wrap(hog, 256 * 1024 * 1024 * 2, 0.5, True)).returncode
    assert local.killed_by_cap(code)
