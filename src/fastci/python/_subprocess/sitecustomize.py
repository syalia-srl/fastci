"""Loaded by every Python process that `fastci map record` starts, through PYTHONPATH.

In a child process started by a test, coverage (started by its own subprocess
patch) gets that test's id as its context, and the repository files the child
opens are written next to the map. Outside a test it does nothing.
"""

import os

_ctx = os.environ.get("FASTCI_CTX")
_out = os.environ.get("FASTCI_RECORD")

if _ctx and _out:
    try:
        import coverage

        _cov = coverage.Coverage.current()
        if _cov is not None:
            _cov.switch_context(_ctx)
    except Exception:
        pass

    import atexit
    import json
    import sys

    _root = os.path.realpath(os.environ.get("FASTCI_ROOT", os.getcwd()))
    _seen: set = set()
    _busy = [False]

    def _audit(event, args):
        if event != "open" or _busy[0] or not args:
            return
        path = args[0]
        if isinstance(path, bytes):
            path = path.decode(errors="ignore")
        if not isinstance(path, str):
            return
        _busy[0] = True
        try:
            full = os.path.realpath(os.path.join(os.getcwd(), path))
            if full.startswith(_root + os.sep):
                rel = os.path.relpath(full, _root)
                if not rel.startswith((".venv", ".fastci")) and "__pycache__" not in rel:
                    _seen.add(rel)
        except Exception:
            pass
        finally:
            _busy[0] = False

    sys.addaudithook(_audit)

    @atexit.register
    def _write():
        if _seen:
            os.makedirs(_out, exist_ok=True)
            with open(os.path.join(_out, f"sub-{os.getpid()}.json"), "w") as f:
                json.dump({_ctx: sorted(_seen)}, f)
