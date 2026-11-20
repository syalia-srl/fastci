"""Flaky tests: what a change reaches, and the verdict on a test that failed and then passed."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .config import FlakyConfig
from .python.pick import Pick


@dataclass(frozen=True)
class Reach:
    """The tests a change reaches; ``tests=None`` means every test."""

    tests: frozenset[str] | None
    files: frozenset[str] = frozenset()  # test files reached whole
    why: dict[str, str] = field(default_factory=dict)  # "*": why every test is reached
    since: str = ""  # on main, the sha of the last green run the diff starts at
    known: frozenset[str] | None = None  # the map's tests; None: not checked

    def __contains__(self, nodeid: str) -> bool:
        if self.tests is None:
            return True
        return nodeid in self.tests or nodeid.split("::")[0] in self.files or self.unmapped(nodeid)

    def unmapped(self, nodeid: str) -> bool:
        """A test the map does not know (marked, skipped where it was recorded, or
        added since): fastci cannot tell what reaches it."""
        return self.known is not None and nodeid not in self.known

    @classmethod
    def everything(cls, why: str = "") -> Reach:
        return cls(None, why={"*": why} if why else {})


def reach_of(
    pick: Pick | None,
    changed_tests: set[str],
    share: float,
    known: frozenset[str] | None = None,
) -> Reach:
    """What ``pick`` reaches: tests behind a unit at most ``share`` of the suite ran,
    changed test files, and tests not ``known`` to the map. No pick (no map, no
    merge-base) reaches everything, and so does a full pick, through the path that
    changed."""
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
        known=known,
    )


UNJUDGEABLE = "fastci cannot tell what this change reaches"
UNJUDGEABLE_SINCE = "fastci cannot tell what the commits since the last green run reach"
UNMAPPED = "the map does not know this test"


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
            elif t not in reach.tests and t.split("::")[0] not in reach.files:
                why = UNMAPPED
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


HISTORY = ".fastci/flaky.json"
KEEP = 50  # outcomes on main kept per test


@dataclass
class Entry:
    """A test's outcomes on main: ``main`` holds 1 for a failure, newest last."""

    main: list[int] = field(default_factory=list)
    first: dict | None = None  # the first failure: {"sha", "run", "date", "error"}
    last_failure: dict | None = None
    issue: int | None = None
    landed: bool = False  # a change that reaches the test has landed on main
    clean: int = 0  # runs on main without a failure since it landed


@dataclass
class History:
    """``.fastci/flaky.json``: the outcomes on main of the flagged tests and of those
    that passed on retry, by node id."""

    tests: dict[str, Entry] = field(default_factory=dict)

    @classmethod
    def read(cls, path: str | Path) -> History:
        """The history in ``path``; OSError or ValueError when it is not one. An entry
        it cannot read is left out, and a key it does not know is ignored."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("tests"), dict):
            raise ValueError(f"{path} is not a flaky history")
        known = {f.name for f in fields(Entry)}
        tests = {}
        for t, e in data["tests"].items():
            if isinstance(e, dict):
                entry = Entry(**{k: v for k, v in e.items() if k in known})
                if isinstance(entry.main, list) and all(x in (0, 1) for x in entry.main):
                    tests[t] = entry
        return cls(tests)

    @classmethod
    def load(cls, path: str | Path) -> History:
        """The history in ``path``; empty when the file is missing or unreadable."""
        try:
            return cls.read(path)
        except (OSError, ValueError):
            return cls()

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tests = {t: asdict(e) for t, e in self.tests.items()}
        p.write_text(json.dumps({"version": 1, "tests": tests}, indent=1), encoding="utf-8")

    def record(self, id: str, failed: bool, where: dict) -> Entry:
        """Add one outcome on main; ``where`` says which run, for a failure."""
        e = self.tests.setdefault(id, Entry())
        e.main = [*e.main, int(failed)][-KEEP:]
        if failed:
            e.first = e.first or where
            e.last_failure = where
        return e

    def rate(self, id: str) -> tuple[int, int]:
        """(failed, runs) on main."""
        e = self.tests.get(id)
        return (sum(e.main), len(e.main)) if e else (0, 0)
