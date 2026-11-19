"""``fastci setup``: read a repository and propose its config, workflow and test marks.

Setup never runs the suite. It proposes; ``--write`` applies.
"""

from __future__ import annotations

import ast
import difflib
import os
import re
import subprocess
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import fastci

from .adapter import adapters
from .config import load
from .python.mapfile import TestMap
from .python.pick import is_test_file

WORKFLOW = ".github/workflows/fastci.yml"
# Commands fastci runs itself; a workflow step made only of these is covered.
COVERED = re.compile(r"\b(pytest|ruff|ty check|mypy|pyright|uv sync|uv lock|fastci)\b")
GPU = ("torch", "cupy")
BROWSER = ("playwright", "selenium")


@dataclass(frozen=True)
class Change:
    path: str  # repository-relative
    kind: str  # "add-block" | "add-file" | "append-line" | "mark" | "replace-workflow"
    before: str  # current text ("" for a new file)
    after: str  # proposed text ("" deletes the file)
    reason: str


def _read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text() if p.exists() else ""


def default_branch(root: str) -> str:
    out = subprocess.run(
        ["git", "-C", root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip().removeprefix("origin/") or "main" if out.returncode == 0 else "main"


def detect_summary(root: str) -> str:
    projects = [p for a in adapters() for p in a.detect(root)]
    if not projects:
        return "no project found"
    order = ["uv", "pytest", "xdist", "ruff", "ty", "mypy", "pyright"]
    names = {"xdist": "pytest-xdist"}
    parts = []
    for p in projects:
        parts.append(" · ".join([p.adapter, *(names.get(t, t) for t in order if t in p.tools)]))
    return "; ".join(parts)


def _workflow(branch: str, args: list[str]) -> str:
    text = (files("fastci") / "templates" / "fastci.yml").read_text()
    return (
        text.replace("@@VERSION@@", fastci.__version__)
        .replace("@@BRANCH@@", branch)
        .replace("@@ARGS@@", " ".join(args) or "-q")
    )


def _imports(tree: ast.Module) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def _class_mark(src: str) -> str | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    if "pytestmark" in src:
        return None
    mods = _imports(tree)
    if any(m in mods for m in BROWSER):
        return "browser"
    if "cupy" in mods or ("torch" in mods and ".cuda" in src):
        return "gpu"
    return None


def _add_module_mark(src: str, mark: str) -> str:
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    last = max(
        (n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))),
        default=0,
    )
    head, tail = "".join(lines[:last]), "".join(lines[last:])
    has_pytest = "pytest" in _imports(tree)
    add = ("" if has_pytest else "import pytest\n") + f"\npytestmark = pytest.mark.{mark}\n"
    return head + add + tail


def _add_slow(src: str, names: set[str]) -> str:
    out = []
    for line in src.splitlines(keepends=True):
        m = re.match(r"(\s*)(async\s+)?def (\w+)\(", line)
        if m and m.group(3) in names and not (out and "pytest.mark.slow" in out[-1]):
            out.append(f"{m.group(1)}@pytest.mark.slow\n")
        out.append(line)
    text = "".join(out)
    if "import pytest" not in text:
        text = "import pytest\n\n" + text
    return text


def _marks(root: Path, from_map: str | None, slow_seconds: float) -> list[Change]:
    slow: dict[str, set[str]] = {}
    if from_map:
        tm = TestMap.load(from_map)
        for t, d in zip(tm.tests, tm.durations, strict=True):
            if d > slow_seconds:
                path, name = t.split("::")[0], t.split("::")[-1].split("[")[0]
                slow.setdefault(path, set()).add(name)
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if not is_test_file(rel) or rel.startswith((".venv/", ".git/")) or "/.venv/" in rel:
            continue
        src = p.read_text()
        after, why = src, []
        mark = _class_mark(src)
        if mark:
            after = _add_module_mark(after, mark)
            why.append(f"{mark}: imports {'/'.join(BROWSER if mark == 'browser' else GPU)}")
        if rel in slow:
            names = {n for n in slow[rel] if not re.search(rf"mark\.slow\s*\n\s*def {n}\(", src)}
            if names:
                after = _add_slow(after, names)
                why.append(f"slow: {', '.join(sorted(names))} over {slow_seconds:g} s")
        if after != src:
            out.append(Change(rel, "mark", src, after, "; ".join(why)))
    return out


def _steps(text: str) -> list[tuple[int, int, str]]:
    """(first line, end line, block text) of every list item under a ``steps:`` key."""
    lines = text.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        m = re.match(r"(\s*)steps:\s*$", lines[i])
        if not m:
            i += 1
            continue
        indent, i = len(m.group(1)), i + 1
        while i < len(lines):
            s = lines[i]
            if s.strip() and len(s) - len(s.lstrip()) <= indent:
                break
            if s.lstrip().startswith("- "):
                start, item = i, len(s) - len(s.lstrip())
                i += 1
                while i < len(lines) and (
                    not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > item
                ):
                    i += 1
                out.append((start, i, "".join(lines[start:i])))
            else:
                i += 1
    return out


def run_lines(block: str) -> list[str]:
    """The shell commands of a step: its ``run:`` value, one per line."""
    m = re.search(r"\brun:\s*(.*)", block)
    if not m:
        return []
    first = m.group(1).strip()
    if first in ("|", ">", "|-", ">-"):
        rest = block[m.end() :].splitlines()
        return [line.strip() for line in rest if line.strip()]
    return [first]


def covered(cmds: list[str], stages: list[str]) -> bool:
    for cmd in cmds:
        parts = [c.strip() for c in re.split(r"&&|;|\|\|", cmd) if c.strip()]
        if not all(COVERED.search(c) or c in stages for c in parts):
            return False
    return True


def on_pull_requests(text: str) -> bool:
    """A workflow that tests changes; release and deploy workflows are left alone."""
    return bool(re.search(r"^\s*pull_request(_target)?\b", text, re.M))


def _workflows(root: Path, stages: list[str]) -> list[Change]:
    out = []
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        rel = wf.relative_to(root).as_posix()
        if rel == WORKFLOW:
            continue
        text = wf.read_text()
        if not on_pull_requests(text):
            continue
        runs = [(a, b, run_lines(blk)) for a, b, blk in _steps(text) if run_lines(blk)]
        drop = [(a, b) for a, b, cmds in runs if covered(cmds, stages)]
        if not drop:
            continue
        keep = [cmds for _, _, cmds in runs if not covered(cmds, stages)]
        if not keep:
            after, why = "", "every step it runs is covered by fastci.yml"
        else:
            lines = text.splitlines(keepends=True)
            gone = {n for a, b in drop for n in range(a, b)}
            after = "".join(line for n, line in enumerate(lines) if n not in gone)
            left = "; ".join(c for cmds in keep for c in cmds)
            why = f"its test and check steps are covered by fastci.yml; kept: {left}"
        out.append(Change(rel, "replace-workflow", text, after, why))
    return out


@dataclass(frozen=True)
class Gap:
    workflow: str  # path
    job: str
    step: str  # the step's name, or its run line's first 60 characters
    run: str


def _job(lines: list[str], at: int) -> str:
    for line in reversed(lines[:at]):
        m = re.match(r"  ([\w-]+):\s*$", line)
        if m:
            return m.group(1)
    return ""


def check(root: str) -> list[Gap]:
    """Each run step of the old workflows that fastci does not cover."""
    r = Path(root)
    stages = [s.run for s in load(root).stages.values() if s.run]
    gaps = []
    for wf in sorted((r / ".github" / "workflows").glob("*.y*ml")):
        rel = wf.relative_to(r).as_posix()
        if rel == WORKFLOW:
            continue
        text = wf.read_text()
        if not on_pull_requests(text):
            continue
        lines = text.splitlines()
        for start, _, block in _steps(text):
            cmds = run_lines(block)
            if not cmds or covered(cmds, stages):
                continue
            name = re.search(r"\bname:\s*(.+)", block)
            run = "; ".join(cmds)
            label = name.group(1).strip() if name else run[:60]
            gaps.append(Gap(rel, _job(lines, start), label, run))
    return gaps


def propose(root: str, from_map: str | None = None) -> list[Change]:
    r = Path(root)
    cfg = load(root)
    branch = default_branch(root)
    changes: list[Change] = []
    if branch != "main" and cfg.source == "defaults" and (r / "pyproject.toml").exists():
        before = _read(r, "pyproject.toml")
        block = f'\n[tool.fastci]\ndefault-branch = "{branch}"\n'
        changes.append(
            Change("pyproject.toml", "add-block", before, before + block, "default branch")
        )
    if not (r / WORKFLOW).exists():
        wf = _workflow(branch, cfg.tests.args)
        changes.append(Change(WORKFLOW, "add-file", "", wf, "the fastci workflow"))
    gi = _read(r, ".gitignore")
    if ".fastci/" not in gi.split():
        after = gi + ("" if not gi or gi.endswith("\n") else "\n") + ".fastci/\n"
        changes.append(Change(".gitignore", "append-line", gi, after, "the local map cache"))
    changes += _marks(r, from_map, cfg.tests.slow_seconds)
    changes += _workflows(r, [s.run for s in cfg.stages.values() if s.run])
    return changes


def diff(changes: list[Change]) -> str:
    out = []
    for c in changes:
        out.extend(
            difflib.unified_diff(
                c.before.splitlines(keepends=True),
                c.after.splitlines(keepends=True),
                f"a/{c.path}" if c.before else "/dev/null",
                f"b/{c.path}" if c.after else "/dev/null",
            )
        )
    return "".join(out)


def apply(root: str, changes: list[Change]) -> None:
    for c in changes:
        p = Path(root) / c.path
        if not c.after:
            p.unlink(missing_ok=True)
            continue
        os.makedirs(p.parent, exist_ok=True)
        p.write_text(c.after)


def summary_lines(changes: list[Change]) -> list[str]:
    verbs = {
        "add-block": "would add  [tool.fastci] to",
        "add-file": "would add ",
        "append-line": "would add  .fastci/ to",
        "mark": "would mark",
        "replace-workflow": "would replace",
    }
    return [f"{verbs[c.kind]} {c.path:<36} {c.reason}" for c in changes]
