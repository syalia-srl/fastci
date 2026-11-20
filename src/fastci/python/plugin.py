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
  read back by ``fastci.results``.
"""

from __future__ import annotations

import json
import os
import sys

RECORD = os.environ.get("FASTCI_RECORD")
SELECT = os.environ.get("FASTCI_SELECT")
DONE = os.environ.get("FASTCI_DONE")
RESULTS = os.environ.get("FASTCI_RESULTS")
ROOT = os.path.realpath(os.environ.get("FASTCI_ROOT", os.getcwd()))

_state: dict = {"test": None, "busy": False}
_opens: dict[str, set[str]] = {}
_durations: dict[str, float] = {}
_always: set[str] = set()


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

    def pytest_runtest_setup(item):
        _state["test"] = item.nodeid
        os.environ["FASTCI_CTX"] = item.nodeid
        if item.get_closest_marker("always"):
            _always.add(item.nodeid)

    def pytest_runtest_logreport(report):
        _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration
        if report.when == "teardown":
            _state["test"] = None
            os.environ["FASTCI_CTX"] = ""

    def pytest_sessionfinish(session):
        os.makedirs(RECORD, exist_ok=True)
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        with open(os.path.join(RECORD, f"tests-{worker}.json"), "w") as f:
            json.dump(
                {
                    "opens": {k: sorted(v) for k, v in _opens.items()},
                    "durations": _durations,
                    "always": sorted(_always),
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
COLLECT = os.environ.get("FASTCI_COLLECT")


_worker = {"is": False}


def pytest_configure(config):
    # An xdist worker's config has workerinput; the environment variable also reaches
    # any pytest a worker starts, which is not a worker.
    _worker["is"] = hasattr(config, "workerinput")
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


if SELECT or DONE:

    def pytest_collection_modifyitems(config, items):
        keep, drop = list(items), []
        if SELECT:
            keep, drop = _select(keep)
        if DONE:
            keep, done = _drop_done(keep)
            drop += done
        if drop:
            config.hook.pytest_deselected(items=drop)
        items[:] = keep


if RESULTS:
    import pytest

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
        if report.outcome != "failed" and report.when != "call":
            return
        crash = getattr(report.longrepr, "reprcrash", None)
        error = crash.message.splitlines()[0] if crash and crash.message else ""
        _write(
            {
                "e": "outcome",
                "id": report.nodeid,
                "when": report.when,
                "outcome": report.outcome,
                "error": error,
                "dur": report.duration,
            }
        )

    def pytest_runtest_logfinish(nodeid, location):
        _write({"e": "finished", "id": nodeid})

    @pytest.hookimpl(optionalhook=True)
    def pytest_handlecrashitem(crashitem, report, sched):
        _write({"e": "crashed", "id": crashitem})
