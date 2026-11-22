"""Tests that run the same code: candidates to parametrize, merge or share a fixture.

Every rule here reads each test's own lines (``Suite.own``): the lines it ran that at
most ``reach_share`` of the suite ran, so start-up code does not make every test look
like every other. The cases of one parametrized test are one entry.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from . import MIN_OWN, NEAR, Suite, base_id, section


def _total(s: Suite) -> dict:
    return {"shared_lines": s.shared_lines, "reach_share": s.reach_share}


def _fixtures(s: Suite, idx: list[int]) -> list[str]:
    tm = s.tm
    if len(tm.requested) != s.n or not idx:
        return []
    common = set.intersection(*(set(tm.requested[i]) for i in idx))
    return sorted({tm.fixtures[f][0] for f in common})


def same_path(s: Suite) -> dict:
    groups: dict[frozenset, list[int]] = defaultdict(list)
    for i, own in enumerate(s.own):
        if len(own) >= MIN_OWN:
            groups[own].append(i)
    out = []
    for own, idx in groups.items():
        bases: dict[str, list[int]] = defaultdict(list)
        for i in idx:
            bases[base_id(s.tm.tests[i])].append(i)
        if len(bases) < 2:
            continue
        secs = {b: sum(s.durations[i] for i in ii) for b, ii in bases.items()}
        out.append(
            {
                "tests": [{"test": b, "cases": len(ii)} for b, ii in sorted(bases.items())],
                "files": len({b.split("::")[0] for b in bases}),
                "own_lines": len(own),
                "seconds": round(sum(secs.values()), 3),
                "beyond_fastest": round(sum(secs.values()) - min(secs.values()), 3),
                "fixtures": _fixtures(s, idx),
            }
        )
    out.sort(key=lambda e: -e["beyond_fastest"])
    total = _total(s) | {"beyond_fastest": round(sum(e["beyond_fastest"] for e in out), 3)}
    return section(out, total)


def near_same(s: Suite) -> dict:
    by_file: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(s.tm.tests):
        if len(s.own[i]) >= MIN_OWN:
            by_file[t.split("::")[0]].append(i)
    parent: dict[int, int] = {}

    def root(i: int) -> int:
        while parent.get(i, i) != i:
            i = parent[i]
        return i

    for idx in by_file.values():
        for a_pos, a in enumerate(idx):
            for b in idx[a_pos + 1 :]:
                A, B = s.own[a], s.own[b]
                if A == B or base_id(s.tm.tests[a]) == base_id(s.tm.tests[b]):
                    continue
                if len(A & B) / len(A | B) >= NEAR:
                    parent[root(b)] = root(a)
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in parent:
        clusters[root(i)].append(i)
    for r in list(clusters):
        if r not in clusters[r]:
            clusters[r].append(r)
    out = []
    for idx in clusters.values():
        core = frozenset.intersection(*(s.own[i] for i in idx))
        out.append(
            {
                "file": s.tm.tests[idx[0]].split("::")[0],
                "tests": [
                    {"test": s.tm.tests[i], "adds": len(s.own[i] - core)} for i in sorted(idx)
                ],
                "core": len(core),
                "seconds": round(sum(s.durations[i] for i in idx), 3),
            }
        )
    out.sort(key=lambda e: -e["seconds"])
    return section(out, _total(s))


def startup_path(s: Suite) -> dict:
    limit = s.reach_share * s.n
    groups: dict[frozenset, list[str]] = defaultdict(list)
    for f, e in s.tm.files.items():
        for q, (_, _, ids) in e.funcs.items():
            if len(ids) > limit:
                groups[frozenset(ids)].append(f"{f}::{q}")
    out = []
    for ids, funcs in groups.items():
        ds = [s.durations[i] for i in ids]
        out.append(
            {
                "functions": sorted(funcs),
                "tests": len(ids),
                # No summed seconds: they are the tests' own time, not the functions' cost.
                "median": round(statistics.median(ds), 4),
            }
        )
    out.sort(key=lambda e: (-e["tests"], -len(e["functions"])))
    return section(out, _total(s))
