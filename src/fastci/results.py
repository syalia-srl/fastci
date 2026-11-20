"""Read the results file the pytest plugin writes under ``FASTCI_RESULTS``.

One JSON line per event: ``started`` before a test, ``outcome`` for each failed
phase and for the call phase, ``finished`` after it, and ``crashed`` when an
xdist worker died running it.
"""

from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass, field

FATAL = {signal.SIGABRT, signal.SIGBUS, signal.SIGSEGV, signal.SIGKILL}


@dataclass
class Results:
    failed: list[str] = field(default_factory=list)
    passed: set[str] = field(default_factory=set)
    crashed: set[str] = field(default_factory=set)
    errors: dict[str, str] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)


def signalled(code: int) -> bool:
    """Whether a process exit code says it died by a signal that marks a crash."""
    return code < 0 or code - 128 in FATAL


def read(path: str, signalled: bool) -> Results | None:
    """The results in the file, or None when pytest never wrote it.

    A test started and never finished counts as crashed only when the run was
    ``signalled``: otherwise pytest stopped on purpose (``-x``, Ctrl-C)."""
    if not os.path.exists(path):
        return None
    r = Results()
    started: list[str] = []
    finished: set[str] = set()
    call_passed: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            ev = json.loads(line)
            nodeid = ev["id"]
            if ev["e"] == "started":
                started.append(nodeid)
            elif ev["e"] == "finished":
                finished.add(nodeid)
            elif ev["e"] == "crashed":
                r.crashed.add(nodeid)
            elif ev["outcome"] == "failed":
                if nodeid not in r.errors:
                    r.failed.append(nodeid)
                    r.errors[nodeid] = ev["error"]
            elif ev["when"] == "call" and ev["outcome"] == "passed":
                call_passed.add(nodeid)
    if signalled:
        r.crashed |= {nodeid for nodeid in started if nodeid not in finished}
    r.passed = call_passed - set(r.failed) - r.crashed
    # A skipped or xfailed test neither passed nor failed: it gets no outcome.
    for nodeid in finished | r.crashed:
        if nodeid in r.errors or nodeid in r.crashed:
            r.outcomes[nodeid] = "failed"
        elif nodeid in r.passed:
            r.outcomes[nodeid] = "passed"
    return r
