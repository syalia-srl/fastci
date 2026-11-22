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
    tm = build(work, data, root, sources)
    tm.peak_rss = peak_rss(pytest_args)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tm.save(out)
    print(
        f"fastci: {len(tm.tests)} tests, {len(tm.files)} source files, "
        f"{len(tm.reads)} files read, written to {out}",
        file=sys.stderr,
    )
    return code


def build(work: str, data: str, root: str, sources: list[str] | None = None) -> TestMap:
    """Turn the coverage data and the plugin's notes into a map."""
    import coverage

    durations: dict[str, float] = {}
    opens: dict[str, set[str]] = {}
    always: set[str] = set()
    notes = _Notes()
    for f in glob.glob(os.path.join(work, "tests-*.json")):
        d = json.loads(Path(f).read_text())
        durations.update(d["durations"])
        always.update(d["always"])
        notes.add(d)
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
    unattributed: dict[str, set[int]] = {}
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
                unattributed.setdefault(rel, set()).add(lineno)
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

    tm = TestMap(
        sha=git(root, "rev-parse", "--verify", "-q", "HEAD"),
        tests=tests,
        durations=[durations.get(t, 0.0) for t in tests],
        always={index[t] for t in always if t in index},
        files=files,
        reads=reads,
        unattributed=unattributed,
    )
    notes.fill(tm, idx)
    exclude = exclusions(os.path.join(work, "coveragerc"))
    unparsed = []
    for rel in source_files(root, sources or default_sources(root)):
        lines = executable_lines(os.path.join(root, rel), exclude)
        tm.executable[rel] = lines or set()
        if lines is None:
            unparsed.append(rel)
    tm.lane["unparsed"] = sorted(unparsed)
    return tm


class _Notes:
    """The plugin's version 3 notes, merged over its tests-*.json files."""

    def __init__(self):
        self.phases: dict[str, list[float]] = {}
        self.requested: dict[str, list[list[str]]] = {}
        self.runs: list[list] = []
        self.spawns: set[str] = set()
        self.deselected: set[str] = set()
        self.skipped: dict[str, str] = {}
        self.args: list[str] = []

    def add(self, d: dict) -> None:
        self.phases.update(d.get("phases", {}))
        self.requested.update(d.get("requested", {}))
        self.runs += d.get("fixture_runs", [])
        self.spawns.update(d.get("spawns", []))
        self.deselected.update(d.get("deselected", []))
        self.skipped.update(d.get("skipped", {}))
        self.args = _own_args(d.get("args") or []) or self.args

    def fill(self, tm: TestMap, idx) -> None:
        table: dict[tuple, int] = {}

        def fixture(name, scope, baseid) -> int:
            key = (name, scope, baseid)
            if key not in table:
                table[key] = len(tm.fixtures)
                tm.fixtures.append(key)
            return table[key]

        tm.fixture_runs = [
            (fixture(name, scope, baseid), idx(test), worker, seconds)
            for name, scope, baseid, test, worker, seconds in self.runs
            if test
        ]
        tm.spawns = {idx(t) for t in self.spawns}
        tm.durations += [0.0] * (len(tm.tests) - len(tm.durations))
        # After the two above, which may add a test the durations did not name.
        tm.phases = [tuple(self.phases.get(t, (0.0, 0.0, 0.0))) for t in tm.tests]
        tm.requested = [[fixture(*r) for r in self.requested.get(t, [])] for t in tm.tests]
        tm.lane = {
            "args": self.args,
            "deselected": sorted(self.deselected),
            "skipped": dict(sorted(self.skipped.items())),
        }


def _own_args(args: list[str]) -> list[str]:
    """The pytest arguments without the plugin and coverage ones the recorder adds."""
    out: list[str] = []
    for a in args:
        if a.startswith("--cov"):
            continue
        if a == "fastci.python.plugin" and out and out[-1] == "-p":
            out.pop()
            continue
        out.append(a)
    return out


OMIT = ("tests", ".venv", "site-packages", "node_modules", ".git")


def source_files(root: str, sources: list[str]) -> list[str]:
    """Every Python file under the sources, minus what the recording omits."""
    out = set()
    for s in sources:
        for p in Path(root, s).rglob("*.py"):
            rel = os.path.relpath(p, root)
            parts = Path(rel).parts
            if any(o in parts for o in OMIT) or p.name.startswith("test_"):
                continue
            out.add(rel)
    return sorted(out)


def exclusions(rc: str) -> str:
    """Coverage's exclusion regex for the recording's config."""
    import coverage

    rules = coverage.Coverage(config_file=rc if os.path.exists(rc) else False).config.exclude_list
    return "|".join(f"(?:{r})" for r in rules)


def executable_lines(path: str, exclude: str) -> set[int] | None:
    """The lines coverage counts as statements; None when the file is not Python."""
    from coverage.parser import PythonParser

    try:
        parser = PythonParser(filename=path, exclude=exclude)
        parser.parse_source()
    except Exception:
        return None
    return set(parser.statements)


def peak_rss(pytest_args: list[str]) -> int:
    """Peak memory of one pytest worker in the run that just ended, in bytes."""
    import resource

    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
    workers = 1
    for i, a in enumerate(pytest_args):
        n = a[2:] if a.startswith("-n") and len(a) > 2 else None
        if a == "-n" and i + 1 < len(pytest_args):
            n = pytest_args[i + 1]
        if n and n.isdigit():
            workers = max(1, int(n))
    return peak // workers


def default_sources(root: str) -> list[str]:
    return ["src"] if os.path.isdir(os.path.join(root, "src")) else ["."]
