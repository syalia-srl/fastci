"""Run a plan: each step in order, stopping at the first that fails."""

from __future__ import annotations

import dataclasses
import itertools
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable

from . import fetch, results
from .adapter import Adapter, Project
from .flaky import UNJUDGEABLE, UNMAPPED, FlakyRun, Verdict, judge, strip_xdist
from .local import killed_by_cap
from .plan import Step
from .tier import Context

TEST_STAGES = ("always", "tests", "marked")
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
    cap_note: str | None = None,
    flaky: FlakyRun | None = None,
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
            if flaky is None or flaky.retries == 0:
                code = adapter.run_tests(project, root, s.select, args, {}, wrap)
            else:
                code = _judged(adapter, project, root, s.select, args, wrap, flaky, cap_note)
            code = 0 if code == 5 else code
        else:
            cwd = os.path.join(root, project.root)
            code = subprocess.run(wrap(s.command), cwd=cwd).returncode
        if code != 0:
            rest = [t.stage for t in steps[i + 1 :] if t.action == "run"]
            tail = f"; later stages did not run: {', '.join(rest)}" if rest else ""
            say(f"{s.stage} failed (exit {code}){tail}")
            if cap_note and killed_by_cap(code):
                say(cap_note)
            return code
        ran += 1
    say(f"{ran} stages passed, {skipped} skipped")
    return 0


INDENT = "\n" + " " * 10


def say_verdict(v: Verdict, store: str, since: str = "") -> None:
    """``since``: on main, the last green run's sha the judged diff starts at."""
    if v.kind == "crashed":
        how = f" with {v.why}" if v.why else ""
        lines = [f"killed its process{how}: a fault in the code or the machine, not a flake"]
    elif v.kind == "failed":
        lines = ["failed, then failed again on retry"]
    elif v.kind == "machine":
        lines = ["failed, then passed; red: this looks like the machine, not the code"]
    elif v.kind == "reached":
        if v.why in (UNJUDGEABLE, UNMAPPED):
            lines = [f"failed, then passed; {v.why}, so it counts as reached"]
        elif since:
            lines = [f"failed, then passed; the commits since {since[:7]} reach it through {v.why}"]
        else:
            lines = [f"failed, then passed; this change reaches it through {v.why}"]
        what = "those commits" if since else "the change"
        lines.append(f"red: check for a race in what {what} touched")
    else:
        if since:
            nothing = "no commit since the last green run reaches it"
        else:
            nothing = "this change does not reach it"
        lines = [
            f"failed, then passed; {nothing}",
            "passed in a fresh process: it may depend on test order",
        ]
    say(INDENT.join([v.id, *lines]))


def _signal_name(code: int) -> str:
    if not results.signalled(code):
        return ""
    try:
        return signal.Signals(-code if code < 0 else code - 128).name
    except ValueError:
        return ""


def _stops_early(a: str) -> bool:
    return a in ("-x", "--exitfirst", "--maxfail") or a.startswith("--maxfail=")


def strip_stop_early(args: list[str]) -> list[str]:
    """``args`` without -x, --exitfirst and --maxfail and its value."""
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif _stops_early(a):
            skip = a == "--maxfail"
        else:
            out.append(a)
    return out


def _judged(adapter, project, root, select, args, wrap, flaky: FlakyRun, cap_note) -> int:
    """Run a test stage, retry what failed and judge it: the stage's exit code.

    When pytest says it stopped early (-x or --maxfail, wherever they were set), a
    forgiven failure must not end the stage, so it runs again without the tests
    already passed or judged."""
    rescued: set[str] = set()  # passed on retry, in any run of the stage
    done: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="fastci-") as tmp:
        n = itertools.count()

        def run(sel, a, env):
            path = os.path.join(tmp, f"results-{next(n)}.jsonl")
            code = adapter.run_tests(project, root, sel, a, {**env, "FASTCI_RESULTS": path}, wrap)
            return code, path

        while True:
            env = {}
            if done:
                env["FASTCI_DONE"] = os.path.join(tmp, f"done-{next(n)}.txt")
                with open(env["FASTCI_DONE"], "w", encoding="utf-8") as f:
                    f.write("".join(f"{t}\n" for t in sorted(done)))
            code, path = run(select, args, env)
            if code in (0, 5) or (cap_note and killed_by_cap(code)):
                return code
            if code != 1 and not results.signalled(code):
                return code  # interrupted, an internal or a usage error: no test to blame
            res = results.read(path, results.signalled(code))
            if res is None:
                return code
            failed = [*res.failed, *sorted(res.crashed - set(res.failed))]
            if not failed:
                return code
            retry = [t for t in failed if t not in res.crashed]
            if retry:
                times = "once" if flaky.retries == 1 else f"up to {flaky.retries} times"
                them = "them" if len(retry) > 1 else "it"
                say(f"{len(retry)} failed; retrying {them} {times}, serially, in a fresh process")
            for _ in range(flaky.retries):
                retry = [t for t in retry if t not in rescued]
                if not retry:
                    break
                # --maxfail=0 last: -x in addopts or PYTEST_ADDOPTS comes before it.
                c, p = run(retry, [*strip_stop_early(strip_xdist(args)), "--maxfail=0"], {})
                again = results.read(p, results.signalled(c))
                if again:
                    rescued |= again.passed & set(retry)
            verdicts = judge(failed, rescued, res.crashed, flaky.reach, flaky.cfg)
            machine = [v for v in verdicts if v.kind == "machine"]
            if machine:
                say(
                    f"{len(machine)} tests failed and then passed:"
                    " this looks like the machine, not the code"
                )
            how = _signal_name(code)
            for v in verdicts:
                say_verdict(
                    dataclasses.replace(v, why=how) if v.kind == "crashed" else v,
                    flaky.cfg.store,
                    flaky.reach.since,
                )
            if any(v.blocks for v in verdicts):
                return code
            if not res.explained:
                say(
                    "pytest counted failures no failed test explains"
                    " (a collection error, a coverage threshold or a plugin); red"
                )
                return code
            if not res.stopped:
                return 0
            done |= res.passed | set(failed)


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
    if token or not ctx.spec.capped:
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


def ensure_map(
    root: str, ctx: Context, repo: str | None, branch: str, need: bool = False
) -> str | None:
    """The path of a usable map, fetching main's newest when it is newer than ours.
    A tier that runs every test fetches only when it ``need``s the map anyway."""
    path = os.path.join(root, MAP)
    have = path if os.path.exists(path) else None
    if (ctx.spec.tests == "all" and not need) or repo is None:
        return have
    token = _token(ctx)
    if not token:
        return have
    # On a laptop a dead network must not hold up the checks.
    timeout = 5 if ctx.spec.capped else 60
    try:
        art = fetch.newest_artifact(repo, "fastci", branch, token, timeout=timeout)
        if art is None:
            art = fetch.newest_artifact(repo, "testmap", branch, token, timeout=timeout)
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
