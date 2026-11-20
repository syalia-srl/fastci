"""Flaky tests: what a change reaches, and the verdict on a test that failed and then passed."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import FlakyConfig
from .python.pick import Pick


@dataclass(frozen=True)
class Reach:
    """The tests a change reaches; ``tests=None`` means every test."""

    tests: frozenset[str] | None
    files: frozenset[str] = frozenset()  # test files reached whole
    why: dict[str, str] = field(default_factory=dict)  # "*": why every test is reached
    since: str = ""  # on main, the sha of the last green run the diff starts at

    def __contains__(self, nodeid: str) -> bool:
        if self.tests is None:
            return True
        return nodeid in self.tests or nodeid.split("::")[0] in self.files

    @classmethod
    def everything(cls, why: str = "") -> Reach:
        return cls(None, why={"*": why} if why else {})


def reach_of(pick: Pick | None, changed_tests: set[str], share: float) -> Reach:
    """What ``pick`` reaches: tests behind a unit at most ``share`` of the suite ran,
    and changed test files. No pick (no map, no merge-base) reaches everything, and so
    does a full pick, through the path that changed."""
    if pick is None:
        return Reach.everything()
    if pick.full:
        return Reach.everything(pick.why["*"].removesuffix(" changed"))
    tests = frozenset(t for t, s in pick.reach.items() if s <= share)
    files = {f for f in pick.files if pick.why.get(f) != "listed in always"}
    return Reach(
        tests,
        frozenset(files | changed_tests),
        {t: pick.why[t] for t in tests if t in pick.why},
    )


UNJUDGEABLE = "fastci cannot tell what this change reaches"


@dataclass(frozen=True)
class Verdict:
    """What a failed test means: ``kind`` is crashed, failed, machine, reached or flaky."""

    id: str
    kind: str
    blocks: bool
    why: str = ""


def judge(
    failed: list[str],
    passed_on_retry: set[str],
    crashed: set[str],
    reach: Reach,
    cfg: FlakyConfig,
) -> list[Verdict]:
    """One verdict per failed test, in the order of ``failed``; the first row that
    matches decides (the spec's rule table)."""
    machine = len(passed_on_retry - crashed) >= cfg.machine_threshold
    verdicts = []
    for t in failed:
        if t in crashed:
            verdicts.append(Verdict(t, "crashed", True))
        elif t not in passed_on_retry:
            verdicts.append(Verdict(t, "failed", True))
        elif machine:
            verdicts.append(Verdict(t, "machine", True))
        elif t in reach and not cfg.forgive_reached:
            if reach.tests is None:
                why = reach.why.get("*", UNJUDGEABLE)
            else:
                why = reach.why.get(t, t.split("::")[0])
            verdicts.append(Verdict(t, "reached", True, why))
        else:
            verdicts.append(Verdict(t, "flaky", False))
    return verdicts


@dataclass
class FlakyRun:
    """What a test stage needs to retry its failed tests and judge them."""

    cfg: FlakyConfig
    retries: int
    reach: Reach
    flagger: "Flagger | None" = None  # noqa: F821, UP037


def strip_xdist(args: list[str]) -> list[str]:
    """``args`` without xdist's -n, --numprocesses and --dist and their values."""
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a in ("-n", "--numprocesses", "--dist"):
            skip = True
        elif not a.startswith(("-n", "--numprocesses=", "--dist=")):
            out.append(a)
    return out
