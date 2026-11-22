"""Where the suite spends its time: set-up, slow tests, waits, files, always."""

from __future__ import annotations

import os
import statistics
from collections import defaultdict

from . import LITTLE_CODE_FACTOR, LITTLE_CODE_MIN_S, Suite, base_id, section

NO_V3 = "this map has no {what} (version {v}); re-record it with this fastci"


def _rebuild_key(scope: str, test: str, worker: str):
    """Runs of a wider fixture with the same key are one too many."""
    file = test.split("::")[0]
    if scope == "session":
        return (worker,)
    if scope == "package":
        return (worker, os.path.dirname(file))
    if scope == "module":
        return (worker, file)
    if scope == "class":
        return (worker, test.rpartition("::")[0])
    return None


def setup_cost(s: Suite) -> dict:
    tm = s.tm
    if tm.version < 3:
        return section(note=NO_V3.format(what="fixture runs", v=tm.version))
    runs: dict[int, list] = defaultdict(list)
    for f, t, worker, seconds in tm.fixture_runs:
        runs[f].append((t, worker, seconds))
    takers: dict[int, int] = defaultdict(int)
    for wanted in tm.requested:
        for f in set(wanted):
            takers[f] += 1
    out = []
    for f, rs in runs.items():
        name, scope, baseid = tm.fixtures[f]
        keys: dict = defaultdict(int)
        for t, worker, _ in rs:
            k = _rebuild_key(scope, tm.tests[t], worker)
            if k is not None:
                keys[k] += 1
        out.append(
            {
                "fixture": name,
                "scope": scope,
                "defined_in": baseid,
                "runs": len(rs),
                "tests": takers[f],
                "seconds": round(sum(r[2] for r in rs), 3),
                "rebuilt": sum(c - 1 for c in keys.values()),
            }
        )
    out.sort(key=lambda e: -e["seconds"])
    return section(out, {"seconds": round(sum(e["seconds"] for e in out), 3)})


def slowest(s: Suite) -> dict:
    tm = s.tm
    by: dict[str, dict] = {}
    for i, t in enumerate(tm.tests):
        e = by.setdefault(
            base_id(t),
            {
                "test": base_id(t),
                "cases": 0,
                "seconds": 0.0,
                "setup": 0.0,
                "call": 0.0,
                "teardown": 0.0,
                "marks": [],
            },
        )
        e["cases"] += 1
        e["seconds"] += s.durations[i]
        if s.phases:
            for k, v in zip(("setup", "call", "teardown"), s.phases[i], strict=True):
                e[k] += v
        if i in tm.always and "always" not in e["marks"]:
            e["marks"].append("always")
    out = sorted(by.values(), key=lambda e: -e["seconds"])
    for e in out:
        e["share"] = round(e["seconds"] / s.total, 4) if s.total else 0.0
        for k in ("seconds", "setup", "call", "teardown"):
            e[k] = round(e[k], 3)
    top10 = sum(e["seconds"] for e in out[:10])
    total = {
        "seconds": round(s.total, 3),
        "top10_share": round(top10 / s.total, 4) if s.total else 0,
    }
    note = None if s.phases else NO_V3.format(what="phases", v=tm.version)
    return section(out, total, note if s.n else None)


def little_code(s: Suite) -> dict:
    if not s.phases:
        return section(note=NO_V3.format(what="phases", v=s.tm.version) if s.n else None)
    ratios = [s.phases[i][1] / max(s.lines_run[i], 1) for i in range(s.n)]
    median = statistics.median(ratios) if ratios else 0.0
    out = []
    for i, t in enumerate(s.tm.tests):
        call = s.phases[i][1]
        if call >= LITTLE_CODE_MIN_S and ratios[i] >= LITTLE_CODE_FACTOR * median:
            out.append(
                {
                    "test": t,
                    "call": round(call, 3),
                    "lines": s.lines_run[i],
                    "ratio": round(ratios[i], 4),
                    "spawns": i in s.tm.spawns,
                }
            )
    out.sort(key=lambda e: -e["call"])
    return section(out, {"median_ratio": round(median, 6)})


def by_test_file(s: Suite) -> dict:
    by: dict[str, list[float]] = defaultdict(list)
    for i, t in enumerate(s.tm.tests):
        by[t.split("::")[0]].append(s.durations[i])
    out = [
        {
            "file": f,
            "tests": len(ds),
            "seconds": round(sum(ds), 3),
            "share": round(sum(ds) / s.total, 4) if s.total else 0.0,
            "floor": round(min(ds) * len(ds), 3),
        }
        for f, ds in by.items()
    ]
    out.sort(key=lambda e: -e["seconds"])
    return section(out, {"files": len(out)})


def always_cost(s: Suite) -> dict:
    out = [
        {"test": s.tm.tests[i], "seconds": round(s.durations[i], 3)} for i in sorted(s.tm.always)
    ]
    out.sort(key=lambda e: -e["seconds"])
    return section(out, {"seconds": round(sum(e["seconds"] for e in out), 3)})
