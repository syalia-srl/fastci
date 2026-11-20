"""Flaky tests: what a change reaches, and the verdict on a test that failed and then passed."""

from __future__ import annotations

from dataclasses import dataclass, field

from .python.pick import Pick


@dataclass(frozen=True)
class Reach:
    """The tests a change reaches; ``tests=None`` means every test."""

    tests: frozenset[str] | None
    files: frozenset[str] = frozenset()  # test files reached whole
    why: dict[str, str] = field(default_factory=dict)

    def __contains__(self, nodeid: str) -> bool:
        if self.tests is None:
            return True
        return nodeid in self.tests or nodeid.split("::")[0] in self.files

    @classmethod
    def everything(cls) -> Reach:
        return cls(None)


def reach_of(pick: Pick | None, changed_tests: set[str], share: float) -> Reach:
    """What ``pick`` reaches: tests behind a unit at most ``share`` of the suite ran,
    and changed test files. No pick (no map, no merge-base) reaches everything."""
    if pick is None:
        return Reach.everything()
    if pick.full:
        return Reach(frozenset(), frozenset(changed_tests))
    tests = frozenset(t for t, s in pick.reach.items() if s <= share)
    return Reach(
        tests,
        frozenset(pick.files | changed_tests),
        {t: pick.why[t] for t in tests if t in pick.why},
    )
