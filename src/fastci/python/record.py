"""``fastci map record``: run the suite once, recording what every test runs and reads."""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .mapfile import FileEntry, TestMap, functions, line_hash

SUBPROCESS_DIR = str(Path(__file__).parent / "_subprocess")

COVERAGERC = """[run]
source = {source}
omit = */tests/*,*/test_*.py,*/.venv/*,*/site-packages/*,*/node_modules/*
parallel = true
core = ctrace
data_file = {data}
patch = subprocess
context = ${{FASTCI_CTX}}
"""


def git(root: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, errors="ignore"
    ).stdout.strip()


def record(
    pytest_args: list[str],
    out: str,
    sources: list[str],
    root: str,
    python: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Run pytest with recording on, write the map to ``out``, return pytest's exit code."""
    root = os.path.realpath(root)
    work = tempfile.mkdtemp(prefix="fastci-")
    data = os.path.join(work, "cov", ".coverage")
    os.makedirs(os.path.dirname(data))
    rc = os.path.join(work, "coveragerc")
    source = ",".join(os.path.join(root, s) for s in sources)
    Path(rc).write_text(COVERAGERC.format(source=source, data=data))
    env = dict(env or os.environ)
    env.update(FASTCI_RECORD=work, FASTCI_ROOT=root, FASTCI_CTX="")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [SUBPROCESS_DIR, env.get("PYTHONPATH")]))
    cmd = [
        *(python or [sys.executable]),
        "-m",
        "pytest",
        "-p",
        "fastci.python.plugin",
        *[f"--cov={os.path.join(root, s)}" for s in sources],
        "--cov-context=test",
        f"--cov-config={rc}",
        "--cov-report=",
        *pytest_args,
    ]
    code = subprocess.run(cmd, cwd=root, env=env).returncode
    tm = build(work, data, root)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tm.save(out)
    print(
        f"fastci: {len(tm.tests)} tests, {len(tm.files)} source files, "
        f"{len(tm.reads)} files read, written to {out}",
        file=sys.stderr,
    )
    return code


def build(work: str, data: str, root: str) -> TestMap:
    """Turn the coverage data and the plugin's notes into a map."""
    import coverage

    durations: dict[str, float] = {}
    opens: dict[str, set[str]] = {}
    always: set[str] = set()
    for f in glob.glob(os.path.join(work, "tests-*.json")):
        d = json.loads(Path(f).read_text())
        durations.update(d["durations"])
        always.update(d["always"])
        for t, paths in d["opens"].items():
            opens.setdefault(t, set()).update(paths)
    for f in glob.glob(os.path.join(work, "sub-*.json")):
        for t, paths in json.loads(Path(f).read_text()).items():
            opens.setdefault(t, set()).update(paths)

    tests = sorted(durations)
    index = {t: i for i, t in enumerate(tests)}

    def idx(t: str) -> int:
        if t not in index:
            index[t] = len(tests)
            tests.append(t)
        return index[t]

    cov = coverage.CoverageData(basename=data)
    cov.read()
    files: dict[str, FileEntry] = {}
    for measured in cov.measured_files():
        full = os.path.realpath(measured)
        if not full.startswith(root + os.sep):
            continue
        rel = os.path.relpath(full, root)
        try:
            text = Path(full).read_text(errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        funcs = functions(text)
        entry = FileEntry()
        for lineno, contexts in cov.contexts_by_lineno(measured).items():
            ids = {idx(c.split("|")[0]) for c in contexts if "::" in c}
            if not ids:
                continue
            entry.tests |= ids
            body = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            entry.lines[lineno] = (line_hash(body), ids)
            for name, start, end in funcs:
                if start <= lineno <= end:
                    s, e, held = entry.funcs.get(name, (start, end, set()))
                    entry.funcs[name] = (s, e, held | ids)
        if entry.tests:
            files[rel] = entry

    reads: dict[str, set[int]] = {}
    for t, paths in opens.items():
        for p in paths:
            reads.setdefault(p, set()).add(idx(t))

    return TestMap(
        sha=git(root, "rev-parse", "HEAD"),
        tests=tests,
        durations=[durations.get(t, 0.0) for t in tests],
        always={index[t] for t in always if t in index},
        files=files,
        reads=reads,
    )


def default_sources(root: str) -> list[str]:
    return ["src"] if os.path.isdir(os.path.join(root, "src")) else ["."]
