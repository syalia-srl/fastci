"""``fastci report``: the suite's health, read from main's maps without running a test.

``build`` turns one or more maps, the source tree and the flaky history into a plain
dict of sections; ``render`` prints it as text, JSON or one HTML page. Every finding
carries evidence (seconds, lines, tests), never a verdict: coverage cannot see what a
test asserts, so the reader decides.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..python.mapfile import TestMap

MIN_OWN = 15  # own lines a group of tests needs to be worth reading
NEAR = 0.9  # Jaccard index above which two tests are almost the same
LITTLE_CODE_MIN_S = 0.5  # call seconds below which a test is never "much time, little code"
LITTLE_CODE_FACTOR = 10  # times the suite's median seconds per line run
TOP = 10  # entries per section in the text
MIN_FIXTURE_S = 0.1  # a fixture whose runs cost less in all is not worth a line


def base_id(nodeid: str) -> str:
    """The node id without the parameters of its last part: one id for a test's cases."""
    # Parameters start at the first bracket and may hold "::" or brackets themselves.
    i = nodeid.find("[")
    return nodeid[:i] if i > 0 and nodeid.endswith("]") else nodeid


def section(entries=None, total=None, note=None) -> dict:
    return {"entries": entries or [], "total": total or {}, "note": note}


def empty_map() -> TestMap:
    return TestMap(sha="", tests=[], durations=[], always=set(), files={}, reads={})


@dataclass
class Suite:
    """The newest map and what the sections derive from it."""

    maps: list[TestMap]
    root: str
    reach_share: float
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.tm = self.maps[0] if self.maps else empty_map()
        tm = self.tm
        self.n = len(tm.tests)
        seen: dict[str, list[float]] = {}
        for m in self.maps:
            for t, d in zip(m.tests, m.durations, strict=False):
                seen.setdefault(t, []).append(d)
        self.durations = [statistics.median(seen.get(t) or [0.0]) for t in tm.tests]
        self.total = sum(self.durations)
        self.phases = tm.phases if len(tm.phases) == self.n else []
        limit = self.reach_share * self.n
        own: list[set] = [set() for _ in range(self.n)]
        self.lines_run = [0] * self.n
        self.shared_lines = 0
        for f, e in tm.files.items():
            for line, (_, ids) in e.lines.items():
                for i in ids:
                    self.lines_run[i] += 1
                if len(ids) <= limit:
                    for i in ids:
                        own[i].add((f, line))
                else:
                    self.shared_lines += 1
        self.own = [frozenset(s) for s in own]


def build(suite: Suite, history=None, top: int = TOP) -> dict:
    """The whole report as a dict. ``top`` is for the renderers; the model keeps every entry."""
    from . import coverage, overlap, time

    cov = {"by_file": coverage.by_file(suite), "never_imported": coverage.never_imported(suite)}
    return {
        "version": 1,
        "header": coverage.header(suite),  # after by_file, which adds notes
        "time": {
            "setup_cost": time.setup_cost(suite),
            "slowest": time.slowest(suite),
            "little_code": time.little_code(suite),
            "by_test_file": time.by_test_file(suite),
            "always_cost": time.always_cost(suite),
        },
        "overlap": {
            "same_path": overlap.same_path(suite),
            "near_same": overlap.near_same(suite),
            "startup_path": overlap.startup_path(suite),
        },
        "coverage": cov,
        "flaky": coverage.flaky_section(history),
    }
