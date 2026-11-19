"""The plan: which stages run in this case, in which order, and why.

Checks of every project come first (cheapest), then the tests marked always,
then the tests a change can break, then, on a release, the release-only classes.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass

from .adapter import Check, Project
from .config import Config
from .python.mapfile import TestMap
from .python.pick import Pick, is_test_file
from .tier import Context

CHECK_ORDER = ("lock", "format", "lint", "types")


@dataclass(frozen=True)
class Step:
    stage: str  # "lock" | "format" | "lint" | "types" | "always" | "tests" | "release" | config
    project: str  # project root
    action: str  # "run" | "skip"
    command: list[str]  # argv for checks; pytest args for test stages; empty when skipped
    select: list[str] | None  # test ids or files for test stages; None = no selection
    reason: str


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


def changed_files(root: str, base: str) -> set[str] | None:
    """Files changed since the merge-base with ``base``, working tree and untracked included."""
    mb = _git(root, "merge-base", base, "HEAD")
    if mb.returncode != 0:
        return None
    diff = _git(root, "diff", "--name-only", "--no-renames", mb.stdout.strip())
    new = _git(root, "ls-files", "--others", "--exclude-standard")
    return set(diff.stdout.split()) | set(new.stdout.split())


def _checks(project: Project, cfg: Config, tier: str) -> list[Check]:
    checks = []
    for c in project.checks:
        spec = cfg.stages.get(c.name)
        if spec is None:
            checks.append(c)
        elif spec.run and tier in spec.tiers:
            checks.append(Check(c.name, spec.run, c.fix, c.applies_to))
    names = {c.name for c in project.checks}
    for name, spec in cfg.stages.items():
        if name in names or not spec.run or tier not in spec.tiers:
            continue
        extra = Check(name, spec.run, None, ())
        at = next((i + 1 for i, c in enumerate(checks) if c.name == spec.after), len(checks))
        checks.insert(at, extra)
    return checks


def _matches(paths: set[str], globs: tuple[str, ...]) -> bool:
    return not globs or any(fnmatch.fnmatch(p, g) for p in paths for g in globs)


def _by_duration(ids: set[str], tm: TestMap | None) -> list[str]:
    if tm is None:
        return sorted(ids)
    d = dict(zip(tm.tests, tm.durations, strict=True))
    return sorted(ids, key=lambda t: (d.get(t, 0.0), t))


def _test_steps(
    ctx: Context, cfg: Config, p: Project, changed, pick: Pick | None, tm: TestMap | None, gpu
) -> list[Step]:
    args, rel = list(cfg.tests.args), cfg.tests.release_only
    fast = [*args, "-m", f"not ({' or '.join(rel)})"] if rel else args
    run = lambda stage, cmd, sel, why: Step(stage, p.root, "run", cmd, sel, why)  # noqa: E731
    skip = lambda stage, why: Step(stage, p.root, "skip", [], None, why)  # noqa: E731
    steps = []

    always_ids = {tm.tests[i] for i in tm.always} if tm else set()
    if tm is not None and not always_ids and not cfg.tests.always:
        steps.append(skip("always", "no test is marked always"))
    else:
        steps.append(run("always", [*args, "-m", "always"], None, "tests marked always"))

    if ctx.tier in ("T2", "T3"):
        steps.append(run("tests", fast, None, "the whole fast suite"))
    elif changed is None:
        steps.append(run("tests", fast, None, f"no merge-base with {ctx.base}; every test runs"))
    elif pick is None and ctx.tier == "T1":
        steps.append(run("tests", fast, None, "no map; every test runs"))
    elif pick is None:
        sel = sorted({f for f in changed if is_test_file(f)} | set(cfg.tests.always))
        why = "no map; only changed test files, main runs the rest"
        steps.append(run("tests", fast, sel, why) if sel else skip("tests", why))
    elif pick.full:
        steps.append(run("tests", fast, None, f"{pick.why['*']}; every test runs"))
    else:
        ids = (pick.tests - always_ids) | pick.files | {f for f in changed if is_test_file(f)}
        if ids:
            steps.append(run("tests", fast, _by_duration(ids, tm), f"{len(ids)} picked"))
        else:
            steps.append(skip("tests", "no test can be affected by this change"))

    if ctx.tier == "T3" and rel:
        marks = [m for m in rel if gpu or m != "gpu"]
        why = "release-only classes" + ("" if gpu or "gpu" not in rel else "; gpu left out: no GPU")
        if marks:
            steps.append(run("release", [*args, "-m", " or ".join(marks)], None, why))
    return steps


def build(
    ctx: Context,
    cfg: Config,
    root: str,
    projects: list[Project],
    changed: set[str] | None,
    picks: dict[str, Pick | None],
    maps: dict[str, TestMap | None],
    gpu: bool,
) -> list[Step]:
    early = ctx.tier in ("T0", "T1") and changed is not None
    steps: list[Step] = []
    for p in projects:
        for c in _checks(p, cfg, ctx.tier):
            if early and not _matches(changed, c.applies_to):
                steps.append(Step(c.name, p.root, "skip", [], None, "no changed file matches"))
            else:
                why = "a changed file matches" if early and c.applies_to else "runs in this tier"
                steps.append(Step(c.name, p.root, "run", ["sh", "-c", c.run], None, why))
    for p in projects:
        if "pytest" in p.tools:
            steps += _test_steps(ctx, cfg, p, changed, picks.get(p.root), maps.get(p.root), gpu)
    return steps


def render(ctx: Context, steps: list[Step]) -> str:
    lines = [ctx.reason]
    for n, s in enumerate(steps, 1):
        if s.action == "skip":
            what = "skipped"
        elif s.stage in ("always", "tests", "release"):
            sel = "all" if s.select is None else f"{len(s.select)} selected"
            what = f"pytest {' '.join(s.command)} ({sel})".replace("  ", " ")
        else:
            what = s.command[-1]
        lines.append(f"{n}. {s.stage:<8} {what}   · {s.reason}")
    return "\n".join(lines)


def gather(root: str, ctx: Context, cfg: Config, map_path: str):
    """Projects, changed files, picks and maps for ``build``."""
    from .adapter import adapters
    from .python.pick import Picker

    projects = [p for a in adapters() for p in a.detect(root)]
    changed = changed_files(root, ctx.base) if ctx.base else None
    tm = TestMap.load(map_path) if os.path.exists(map_path) else None
    picks: dict[str, Pick | None] = {}
    maps: dict[str, TestMap | None] = {}
    for p in projects:
        maps[p.root] = tm if p.adapter == "python" else None
        picks[p.root] = None
        if maps[p.root] is not None and changed is not None and ctx.base:
            mb = _git(root, "merge-base", ctx.base, "HEAD").stdout.strip()
            picks[p.root] = Picker(tm, root, cfg.tests).pick(mb)
    return projects, changed, picks, maps
