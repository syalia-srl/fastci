"""What the suite leaves untested, what the map cannot see, and what flakes.

A line counts as run when a test ran it or it ran outside any test (at import, during
collection): ``TestMap.unattributed``. Without those, every module imported during
collection looks partly untested.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from ..python.mapfile import line_hash
from ..python.record import exclusions, executable_lines
from . import Suite, section

MAIN = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]""", re.M)
SCRIPT_DIRS = {"scripts", "tools", "bin"}
DEFAULT_EXCLUDE = exclusions("")


def function_bodies(source: str) -> list[tuple[str, int, int, int]]:
    """(qualified name, def line, first body line, last line) of every function."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                if not isinstance(child, ast.ClassDef):
                    end = child.end_lineno or child.lineno
                    out.append((name, child.lineno, child.body[0].lineno, end))
                walk(child, name + ".")

    walk(tree, "")
    return out


def _git(root: str, *args: str) -> str | None:
    p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def _run(s: Suite, f: str) -> set[int]:
    e = s.tm.files.get(f)
    return (set(e.lines) if e else set()) | s.tm.unattributed.get(f, set())


def _untested(s: Suite, f: str, run: set[int]) -> list[dict] | None:
    path = Path(s.root, f)
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        s.notes.append(f"{f} is not in the tree; its functions are not listed")
        return None
    lines = text.splitlines()
    e = s.tm.files.get(f)
    for n, (h, _) in e.lines.items() if e else []:
        if n > len(lines) or line_hash(lines[n - 1]) != h:
            s.notes.append(f"{f} changed since the map; its functions are not listed")
            return None
    if executable_lines(str(path), DEFAULT_EXCLUDE) != s.tm.executable.get(f):
        s.notes.append(f"{f} changed since the map; its functions are not listed")
        return None
    funcs = function_bodies(text)
    entered = {q: any(n in run for n in range(b, end + 1)) for q, _, b, end in funcs}
    out = []
    for q, d, b, end in funcs:
        if entered[q]:
            continue
        parents = [p for p, pd, _, pe in funcs if pd < d and end <= pe]
        if parents and not entered[max(parents, key=lambda p: len(p))]:
            continue
        out.append({"function": q, "lines": end - b + 1})
    out.sort(key=lambda u: -u["lines"])
    return out


def _modules(s: Suite) -> dict[str, str]:
    """Dotted module name -> source path, for every executable file."""
    out = {}
    for f in s.tm.executable:
        parts = Path(f).with_suffix("").parts
        for start in range(len(parts)):
            name = parts[start:]
            if name[-1] == "__init__":
                name = name[:-1]
            if name:
                out.setdefault(".".join(name), f)
    return out


def _outside_lane(s: Suite) -> set[str]:
    lane = s.tm.lane
    files = {t.split("::")[0] for t in [*lane.get("deselected", []), *lane.get("skipped", {})]}
    mods = _modules(s)
    out = set()
    for tf in files:
        try:
            tree = ast.parse(Path(s.root, tf).read_text())
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            out.update(mods[n] for n in names if n in mods)
    return out


def by_file(s: Suite) -> dict:
    tm = s.tm
    if tm.version < 3:
        return section(
            note=f"this map has no executable lines (version {tm.version}); "
            "re-record it with this fastci"
        )
    lane = _outside_lane(s)
    # Files a test that started a child process read or ran: their coverage may hang on
    # what that child did, which the map sees only when the child kept fastci's variables.
    spawned = {f for f, ids in tm.reads.items() if ids & tm.spawns}
    spawned |= {f for f, e in tm.files.items() if e.tests & tm.spawns}
    out = []
    packages: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f, exe in tm.executable.items():
        if not exe:
            continue
        run = _run(s, f) & exe
        pkg = os.path.dirname(f) or "."
        packages[pkg][0] += len(exe)
        packages[pkg][1] += len(run)
        tags = [t for t, hit in (("outside the lane", f in lane), ("spawns", f in spawned)) if hit]
        out.append(
            {
                "file": f,
                "package": pkg,
                "lines": len(exe),
                "run": len(run),
                "percent": round(100 * len(run) / len(exe), 1),
                "tests": len(tm.files[f].tests) if f in tm.files else 0,
                "untested": _untested(s, f, run) if run else [],
                "tags": tags,
            }
        )
    out.sort(key=lambda e: -(e["lines"] - e["run"]))
    lines, run = sum(e["lines"] for e in out), sum(e["run"] for e in out)
    total = {
        "lines": lines,
        "run": run,
        "percent": round(100 * run / lines, 1) if lines else 100.0,
        "packages": {
            p: {"lines": n, "run": r, "percent": round(100 * r / n, 1)}
            for p, (n, r) in sorted(packages.items())
        },
    }
    return section(out, total)


def _named_in_docs(root: str) -> str:
    listed = _git(root, "ls-files", "*.md", "Makefile", "justfile") or ""
    text = []
    for f in listed.splitlines():
        try:
            text.append(Path(root, f).read_text(errors="ignore"))
        except OSError:
            pass
    return "\n".join(text)


def never_imported(s: Suite) -> dict:
    tm = s.tm
    if tm.version < 3:
        return section(note=f"this map has no executable lines (version {tm.version})")
    docs = None
    out = []
    for f, exe in sorted(tm.executable.items()):
        if not exe or _run(s, f) & exe:
            continue
        try:
            text = Path(s.root, f).read_text(errors="ignore")
        except OSError:
            text = ""
        script = bool(MAIN.search(text)) or bool(SCRIPT_DIRS & set(Path(f).parts[:-1]))
        if not script:
            docs = _named_in_docs(s.root) if docs is None else docs
            script = os.path.basename(f) in docs
        out.append({"file": f, "lines": len(exe), "script": script})
    out.sort(key=lambda e: (e["script"], -e["lines"]))
    return section(out, {"files": len(out), "lines": sum(e["lines"] for e in out)})


def header(s: Suite) -> dict:
    tm = s.tm
    head = _git(s.root, "rev-parse", "HEAD")
    skipped = tm.lane.get("skipped", {})
    every = [m.lane.get("skipped", {}) for m in s.maps]
    notes = ["durations were recorded under coverage, which slows Python code unevenly"]
    if tm.version < 3:
        notes.append(f"this map is version {tm.version}: re-record it for every section")
    return {
        "sha": tm.sha,
        "date": _git(s.root, "show", "-s", "--format=%cI", tm.sha) if tm.sha else None,
        "head_differs": (head != tm.sha) if head and tm.sha else None,
        "version": tm.version,
        "tests": s.n,
        "seconds": round(s.total, 3),
        "args": tm.lane.get("args", []),
        "deselected": len(tm.lane.get("deselected", [])),
        "skipped": [
            {"test": t, "reason": r, "every_map": all(m.get(t) == r for m in every)}
            for t, r in sorted(skipped.items())
        ],
        "spawns": len(tm.spawns),
        "shared_lines": s.shared_lines,
        "reach_share": s.reach_share,
        "maps": len(s.maps),
        "unparsed": tm.lane.get("unparsed", []),
        "notes": notes + s.notes,
    }


def flaky_section(history) -> dict:
    if history is None:
        return section(note="no flaky history from main (the fastci-flaky artifact)")
    out = []
    for t, e in history.tests.items():
        failures = sum(e.main)
        if not failures:
            continue
        last = e.last_failure or {}
        out.append(
            {
                "test": t,
                "failures": failures,
                "runs": len(e.main),
                "rate": round(failures / len(e.main), 3),
                "last": {k: last.get(k) for k in ("sha", "date", "run") if last.get(k)},
                "flagged": e.issue is not None,
                "issue": e.issue,
            }
        )
    out.sort(key=lambda e: -e["rate"])
    return section(out, {"tests": len(out)})
