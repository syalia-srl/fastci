"""The pytest side of fastci.

Loaded with ``-p fastci.python.plugin`` or its pytest11 entry point. It does two
unrelated jobs, each switched on by its own environment variable, so one plugin
serves both commands:

- ``FASTCI_RECORD=<dir>`` (set by ``fastci map record``): note, for every test,
  the repository files it opens and how long it takes, and put the test's id in
  ``FASTCI_CTX`` so that a Python subprocess it starts labels its coverage
  with that test (see ``_subprocess/sitecustomize.py``). Lines run per test
  come from coverage itself (``--cov-context=test``).
- ``FASTCI_SELECT=<file>`` (set by ``fastci map run``): keep only the collected
  tests whose node id, or whose file, is listed in that file.
- ``FASTCI_DONE=<file>`` (set when fastci resumes a stage stopped early):
  deselect the tests whose node id is listed in that file.
- ``FASTCI_RESULTS=<file>``: append one JSON line per test event to that file,
  and one for the session when it ends, read back by ``fastci.results``.
- ``FASTCI_FLAGGED=<file>`` (a JSON object ``{nodeid: issue number or null}``)
  and the mark named by ``FASTCI_FLAKY_MARK`` (``quarantine`` by default) flag
  tests as flaky: a flagged test's failure is reported as a non-strict expected
  failure, so it fails neither pytest's exit code nor ``--maxfail``, while the
  results file keeps the real outcome. ``FASTCI_SKIP_FLAGGED=1`` deselects them.
  Only fastci sets one of the two variables; without either, a plain ``pytest``
  run flags nothing. A mark name that is one of fastci's or pytest's own flags
  nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

RECORD = os.environ.get("FASTCI_RECORD")
SELECT = os.environ.get("FASTCI_SELECT")
DONE = os.environ.get("FASTCI_DONE")
RESULTS = os.environ.get("FASTCI_RESULTS")
FLAGGED = os.environ.get("FASTCI_FLAGGED")
FLAKY_MARK = os.environ.get("FASTCI_FLAKY_MARK")
SKIP_FLAGGED = os.environ.get("FASTCI_SKIP_FLAGGED") == "1"
ROOT = os.path.realpath(os.environ.get("FASTCI_ROOT", os.getcwd()))

_state: dict = {"test": None, "busy": False}
_opens: dict[str, set[str]] = {}
_durations: dict[str, float] = {}
_always: set[str] = set()
_phases: dict[str, list[float]] = {}  # set-up, call, teardown seconds
_requested: dict[str, list[list[str]]] = {}  # [name, scope, baseid] per requested fixture
_fixture_runs: list[list] = []  # [name, scope, baseid, test, worker, seconds]
_spawns: set[str] = set()
_deselected: list[str] = []
_skipped: dict[str, str] = {}
PHASE = {"setup": 0, "call": 1, "teardown": 2}


def repo_path(path) -> str | None:
    """The path relative to the repository root, or None when it lies outside it."""
    if isinstance(path, bytes):
        path = path.decode(errors="ignore")
    if not isinstance(path, str):
        return None
    full = os.path.realpath(os.path.join(os.getcwd(), path))
    if not full.startswith(ROOT + os.sep):
        return None
    rel = os.path.relpath(full, ROOT)
    if rel.startswith((".venv", ".git" + os.sep, ".fastci")) or "__pycache__" in rel:
        return None
    return rel


def _audit(event, args):
    if event != "open" or _state["test"] is None or _state["busy"] or not args:
        return
    _state["busy"] = True
    try:
        rel = repo_path(args[0])
        if rel:
            _opens.setdefault(_state["test"], set()).add(rel)
    except Exception:
        pass
    finally:
        _state["busy"] = False


if RECORD:
    sys.addaudithook(_audit)

    _popen_init = subprocess.Popen.__init__

    def _popen(self, *args, **kwargs):
        if _state["test"] is not None:
            _spawns.add(_state["test"])
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(item):
        _state["test"] = item.nodeid
        os.environ["FASTCI_CTX"] = item.nodeid
        if item.get_closest_marker("always"):
            _always.add(item.nodeid)
        info = getattr(item, "_fixtureinfo", None)
        defs = getattr(info, "name2fixturedefs", {}) if info else {}
        _requested[item.nodeid] = [
            [d[-1].argname, d[-1].scope, d[-1].baseid]
            for name in getattr(item, "fixturenames", ())
            if (d := defs.get(name))
        ]

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(fixturedef, request):
        start = time.perf_counter()
        yield
        seconds = time.perf_counter() - start
        test = _state["test"] or getattr(request.node, "nodeid", "")
        _fixture_runs.append(
            [fixturedef.argname, fixturedef.scope, fixturedef.baseid, test, _worker["id"], seconds]
        )

    def pytest_deselected(items):
        _deselected.extend(i.nodeid for i in items)

    def pytest_runtest_logreport(report):
        _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration
        phases = _phases.setdefault(report.nodeid, [0.0, 0.0, 0.0])
        phases[PHASE[report.when]] += report.duration
        if report.skipped and report.when in ("setup", "call"):
            why = report.longrepr
            why = why[2] if isinstance(why, tuple) and len(why) == 3 else str(why)
            _skipped[report.nodeid] = why.removeprefix("Skipped: ")
        if report.when == "teardown":
            _state["test"] = None
            os.environ["FASTCI_CTX"] = ""

    def pytest_sessionfinish(session):
        os.makedirs(RECORD, exist_ok=True)
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        main = _worker["id"] == "main"
        args = list(session.config.invocation_params.args) if main else []
        with open(os.path.join(RECORD, f"tests-{worker}.json"), "w") as f:
            json.dump(
                {
                    "opens": {k: sorted(v) for k, v in _opens.items()},
                    "durations": _durations,
                    "always": sorted(_always),
                    "phases": _phases,
                    "requested": _requested,
                    "fixture_runs": _fixture_runs,
                    "spawns": sorted(_spawns),
                    "deselected": _deselected,
                    "skipped": _skipped,
                    "args": args,
                },
                f,
            )


MARKS = {
    "always": "fastci runs this test in every tier",
    "slow": "fastci runs this test only on a release branch",
    "gpu": "needs a GPU; fastci runs it only on a release branch where a GPU is found",
    "browser": "drives a browser; fastci runs it only on a release branch",
    "e2e": "drives the whole product; fastci runs it only on a release branch",
    "bench": "measures speed; fastci runs it only on a release branch",
}
PYTEST_MARKS = ("skip", "skipif", "xfail", "parametrize", "usefixtures", "filterwarnings")
# Flagging is on only when fastci runs pytest, which sets one of the two variables.
FLAGGING = bool(FLAGGED or FLAKY_MARK)
FLAKY_MARK = FLAKY_MARK or "quarantine"
if FLAKY_MARK in MARKS or FLAKY_MARK in PYTEST_MARKS:
    FLAKY_MARK = None
else:
    MARKS[FLAKY_MARK] = 'a known flaky test, ("#N") its issue; its failure never fails the run'
COLLECT = os.environ.get("FASTCI_COLLECT")


_worker = {"is": False, "id": "main"}


def pytest_configure(config):
    # An xdist worker's config has workerinput; the environment variable also reaches
    # any pytest a worker starts, which is not a worker.
    _worker["is"] = hasattr(config, "workerinput")
    # From the config, not PYTEST_XDIST_WORKER, which also reaches a pytest a worker starts.
    _worker["id"] = config.workerinput["workerid"] if _worker["is"] else "main"
    for name, text in MARKS.items():
        config.addinivalue_line("markers", f"{name}: {text}")


if COLLECT:

    def pytest_collection_finish(session):
        with open(COLLECT, "w") as f:
            for item in session.items:
                marks = sorted({m.name for m in item.iter_markers()})
                f.write(json.dumps({"id": item.nodeid, "marks": marks}) + "\n")


def _select(items) -> tuple[list, list]:
    with open(SELECT) as f:
        order = {}
        for line in f:
            if line.strip():
                order.setdefault(line.strip(), len(order))
    keep, drop = [], []
    for item in items:
        nodeid = item.nodeid
        keys = (nodeid, nodeid.split("[")[0], nodeid.split("::")[0])
        rank = min((order[k] for k in keys if k in order), default=None)
        if rank is None:
            drop.append(item)
        else:
            keep.append((rank, item))
    keep.sort(key=lambda pair: pair[0])
    return [item for _, item in keep], drop


def _drop_done(items) -> tuple[list, list]:
    with open(DONE, encoding="utf-8") as f:
        done = {line.rstrip("\n") for line in f if line.strip()}
    return [i for i in items if i.nodeid not in done], [i for i in items if i.nodeid in done]


_listed: dict[str, int | None] = {}
if FLAGGED:
    with open(FLAGGED, encoding="utf-8") as f:
        _listed = json.load(f)


def _issue(value) -> int | None:
    """The issue number in a mark's argument, ``"#776"`` or ``776``."""
    try:
        return int(str(value).lstrip("#"))
    except ValueError:
        return None


def flag(item) -> tuple[bool, int | None]:
    """Whether the test is flagged as flaky, and its issue number when known."""
    if not FLAGGING:
        return False, None
    number = _listed.get(item.nodeid)
    mark = item.get_closest_marker(FLAKY_MARK) if FLAKY_MARK else None
    if number is None and mark is not None and mark.args:
        number = _issue(mark.args[0])
    return item.nodeid in _listed or mark is not None, number


if SELECT or DONE or SKIP_FLAGGED:

    def pytest_collection_modifyitems(config, items):
        keep, drop = list(items), []
        if SELECT:
            keep, drop = _select(keep)
        if DONE:
            keep, done = _drop_done(keep)
            drop += done
        if SKIP_FLAGGED:
            drop += [i for i in keep if flag(i)[0]]
            keep = [i for i in keep if not flag(i)[0]]
        if drop:
            config.hook.pytest_deselected(items=drop)
        items[:] = keep


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    flagged, number = flag(item)
    if flagged:
        # Read by the results line, which under xdist runs on the controller.
        report.fastci_flagged = True if number is None else number
        if FLAKY_MARK and item.get_closest_marker(FLAKY_MARK) is not None:
            report.fastci_marked = True  # leaving the list, the mark must go by hand
        if report.failed:
            report.fastci_outcome = "failed"
            report.outcome = "skipped"
            report.wasxfail = "flagged flaky" + ("" if number is None else f": #{number}")
    return report


if RESULTS:

    def _write(event: dict) -> None:
        # Under xdist the controller sees every worker's events, and the crashes.
        if _worker["is"]:
            return
        # One write per line in append mode, so lines never interleave.
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def pytest_runtest_logstart(nodeid, location):
        _write({"e": "started", "id": nodeid})

    def pytest_runtest_logreport(report):
        outcome = getattr(report, "fastci_outcome", report.outcome)
        if outcome != "failed" and report.when != "call":
            return
        crash = getattr(report.longrepr, "reprcrash", None)
        error = crash.message.splitlines()[0] if crash and crash.message else ""
        event = {
            "e": "outcome",
            "id": report.nodeid,
            "when": report.when,
            "outcome": outcome,
            "error": error,
            "dur": report.duration,
        }
        if hasattr(report, "fastci_flagged"):
            event["flagged"] = report.fastci_flagged
        if hasattr(report, "fastci_marked"):
            event["marked"] = True
        _write(event)

    def pytest_runtest_logfinish(nodeid, location):
        _write({"e": "finished", "id": nodeid})

    @pytest.hookimpl(optionalhook=True)
    def pytest_handlecrashitem(crashitem, report, sched):
        _write({"e": "crashed", "id": crashitem})

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(session, exitstatus):
        # What pytest itself counted: a collection error, a coverage threshold or a
        # plugin adds to testsfailed without a failed test; -x or --maxfail from
        # addopts or PYTEST_ADDOPTS stop the session without fastci seeing the flag.
        _write(
            {
                "e": "session",
                "testsfailed": session.testsfailed,
                "stopped": bool(session.shouldfail or session.shouldstop),
                "status": int(exitstatus),
            }
        )
