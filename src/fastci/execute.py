"""Run a plan: each step in order, stopping at the first that fails."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable

from . import fetch
from .adapter import Adapter, Project
from .plan import Step
from .tier import Context

TEST_STAGES = ("always", "tests", "release")
MAP = os.path.join(".fastci", "map.json.gz")


def say(msg: str) -> None:
    print(f"fastci: {msg}", file=sys.stderr, flush=True)


def execute(
    steps: list[Step],
    root: str,
    adapters: dict[str, Adapter],
    projects: dict[str, Project],
    fail_fast_tests: bool,
    wrap: Callable[[list[str]], list[str]] = lambda a: a,
) -> int:
    ran = skipped = 0
    for i, s in enumerate(steps):
        if s.action == "skip":
            say(f"{s.stage} skipped: {s.reason}")
            skipped += 1
            continue
        project = projects[s.project]
        say(f"{s.stage}: {s.reason}")
        if s.stage in TEST_STAGES:
            args = [*s.command, *(["--maxfail=1"] if fail_fast_tests else [])]
            adapter = adapters[project.adapter]
            code = adapter.run_tests(project, root, s.select, args, {}, wrap)
            code = 0 if code == 5 else code
        else:
            cwd = os.path.join(root, project.root)
            code = subprocess.run(wrap(s.command), cwd=cwd).returncode
        if code != 0:
            rest = [t.stage for t in steps[i + 1 :] if t.action == "run"]
            tail = f"; later stages did not run: {', '.join(rest)}" if rest else ""
            say(f"{s.stage} failed (exit {code}){tail}")
            return code
        ran += 1
    say(f"{ran} stages passed, {skipped} skipped")
    return 0


def repo_from_url(url: str) -> str | None:
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def repo_of(root: str) -> str | None:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    out = subprocess.run(
        ["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True
    )
    return repo_from_url(out.stdout.strip()) if out.returncode == 0 else None


def _token(ctx: Context) -> str | None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token or ctx.tier != "T0":
        return token
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _sha(path: str) -> str | None:
    from .python.mapfile import TestMap

    try:
        return TestMap.load(path).sha
    except Exception:
        return None


def ensure_map(root: str, ctx: Context, repo: str | None, branch: str) -> str | None:
    """The path of a usable map, fetching main's newest when it is newer than ours."""
    path = os.path.join(root, MAP)
    have = path if os.path.exists(path) else None
    if ctx.tier == "T2" or repo is None:
        return have
    token = _token(ctx)
    if not token:
        return have
    try:
        art = fetch.newest_artifact(repo, "fastci", branch, token)
        if art is None:
            art = fetch.newest_artifact(repo, "testmap", branch, token)
        if art is None:
            say(f"no map uploaded from {branch} in {repo} yet")
            return have
        sha = art["workflow_run"].get("head_sha")
        if have and sha and _sha(have) == sha:
            return have
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fetch._download(art["archive_download_url"], token, path)
        return path
    except Exception as e:
        say(f"could not fetch the map ({e}); {'using the one here' if have else 'no map'}")
        return have
