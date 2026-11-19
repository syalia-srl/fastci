"""The pytest side of testmap.

Loaded with ``-p testmap.plugin``. It does two unrelated jobs, each switched on
by its own environment variable, so one plugin serves both commands:

- ``TESTMAP_RECORD=<dir>`` (set by ``testmap record``): note, for every test,
  the repository files it opens and how long it takes, and put the test's id in
  ``TESTMAP_CTX`` so that a Python subprocess it starts labels its coverage
  with that test (see ``_subprocess/sitecustomize.py``). Lines run per test
  come from coverage itself (``--cov-context=test``).
- ``TESTMAP_SELECT=<file>`` (set by ``testmap run``): keep only the collected
  tests whose node id, or whose file, is listed in that file.
"""

from __future__ import annotations

import json
import os
import sys

RECORD = os.environ.get("TESTMAP_RECORD")
SELECT = os.environ.get("TESTMAP_SELECT")
ROOT = os.path.realpath(os.environ.get("TESTMAP_ROOT", os.getcwd()))

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
    if rel.startswith((".venv", ".git" + os.sep, ".testmap")) or "__pycache__" in rel:
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
        os.environ["TESTMAP_CTX"] = item.nodeid
        if item.get_closest_marker("always"):
            _always.add(item.nodeid)

    def pytest_runtest_logreport(report):
        _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration
        if report.when == "teardown":
            _state["test"] = None
            os.environ["TESTMAP_CTX"] = ""

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


def pytest_configure(config):
    config.addinivalue_line("markers", "always: testmap runs this test in every pull request")


if SELECT:

    def pytest_collection_modifyitems(config, items):
        with open(SELECT) as f:
            wanted = {line.strip() for line in f if line.strip()}
        keep, drop = [], []
        for item in items:
            nodeid = item.nodeid
            if (
                nodeid in wanted
                or nodeid.split("::")[0] in wanted
                or nodeid.split("[")[0] in wanted
            ):
                keep.append(item)
            else:
                drop.append(item)
        if drop:
            config.hook.pytest_deselected(items=drop)
            items[:] = keep
