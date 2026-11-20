"""Run a plan: each step in order, stopping at the first that fails."""

from __future__ import annotations

import dataclasses
import datetime
import itertools
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence

from . import fetch, issues, results
from .adapter import Adapter, Project
from .config import FlakyConfig
from .flaky import (
    HISTORY,
    UNJUDGEABLE,
    UNJUDGEABLE_SINCE,
    UNMAPPED,
    Entry,
    FlakyRun,
    History,
    Reach,
    Verdict,
    judge,
    strip_xdist,
)
from .local import killed_by_cap
from .plan import Step
from .tier import Context

TEST_STAGES = ("always", "tests", "marked")
MAP = os.path.join(".fastci", "map.json.gz")
FLAGGED = os.path.join(".fastci", "flagged.json")


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
    env: dict[str, str] | None = None  # the flags, read before the first test stage
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
            if env is None:
                env = {}
                # retries = 0 turns the feature off: no flag applies, nothing is read.
                if flaky is not None and flaky.retries > 0 and flaky.flagger is not None:
                    flaky.flagger.prepare(env)
            if flaky is None or flaky.retries == 0:
                code = adapter.run_tests(project, root, s.select, args, env, wrap)
            else:
                code = _judged(adapter, project, root, s.select, args, wrap, flaky, cap_note, env)
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


def say_verdict(v: Verdict, store: str, since: str = "", notes: Sequence[str] = ()) -> None:
    """``since``: on main, the last green run's sha the judged diff starts at; ``notes``:
    the flag's lines, which close the verdict's block."""
    if v.kind == "crashed":
        how = f" with {v.why}" if v.why else ""
        lines = [f"killed its process{how}: a fault in the code or the machine, not a flake"]
    elif v.kind == "failed":
        lines = ["failed, then failed again on retry"]
    elif v.kind == "machine":
        lines = ["failed, then passed; red: this looks like the machine, not the code"]
    elif v.kind == "reached":
        if v.why in (UNJUDGEABLE, UNJUDGEABLE_SINCE, UNMAPPED):
            lines = [f"failed, then passed; {v.why}, so it counts as reached"]
        elif since:
            lines = [f"failed, then passed; the commits since {since[:7]} reach it through {v.why}"]
        else:
            lines = [f"failed, then passed; this change reaches it through {v.why}"]
        what = "those commits" if since or v.why == UNJUDGEABLE_SINCE else "the change"
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
    say(INDENT.join([v.id, *lines, *notes]))


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


def _judged(adapter, project, root, select, args, wrap, flaky: FlakyRun, cap_note, base) -> int:
    """Run a test stage, retry what failed and judge it: the stage's exit code.

    When pytest says it stopped early (-x or --maxfail, wherever they were set), a
    forgiven failure must not end the stage, so it runs again without the tests
    already passed or judged. ``base`` is the environment of every pytest run."""
    rescued: set[str] = set()  # passed on retry, in any run of the stage
    done: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="fastci-") as tmp:
        n = itertools.count()

        def run(sel, a, env):
            path = os.path.join(tmp, f"results-{next(n)}.jsonl")
            code = adapter.run_tests(project, root, sel, a, {**env, "FASTCI_RESULTS": path}, wrap)
            return code, path

        def flag(verdicts, res) -> dict[str, list[str]]:
            if flaky.flagger is not None and res is not None:
                return flaky.flagger.after(verdicts, res, flaky.reach)
            return {}

        while True:
            env = dict(base)
            if done:
                env["FASTCI_DONE"] = os.path.join(tmp, f"done-{next(n)}.txt")
                with open(env["FASTCI_DONE"], "w", encoding="utf-8") as f:
                    f.write("".join(f"{t}\n" for t in sorted(done)))
            code, path = run(select, args, env)
            if code in (0, 5):
                if flaky.flagger is not None:  # main counts the flagged tests that passed
                    flag([], results.read(path, False))
                return code
            if cap_note and killed_by_cap(code):
                return code
            if code != 1 and not results.signalled(code):
                return code  # interrupted, an internal or a usage error: no test to blame
            res = results.read(path, results.signalled(code))
            if res is None:
                return code
            failed = [*res.failed, *sorted(res.crashed - set(res.failed))]
            if not failed:
                flag([], res)
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
                c, p = run(retry, [*strip_stop_early(strip_xdist(args)), "--maxfail=0"], base)
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
            notes = flag(verdicts, res)
            for v in verdicts:
                say_verdict(
                    dataclasses.replace(v, why=how) if v.kind == "crashed" else v,
                    flaky.cfg.store,
                    flaky.reach.since,
                    notes.get(v.id, []),
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
            # A flagged test that ran is done too, whatever its outcome.
            done |= res.passed | set(failed) | res.flagged.keys()


class Flagger:
    """Reads the flagged tests before the test stages and writes the flags after a
    verdict: issues or mark lines, and on main the history. Nothing GitHub answers
    changes a verdict: a failure costs one line here and one in ``lines``."""

    def __init__(
        self,
        cfg: FlakyConfig,
        ctx: Context,
        root: str,
        repo: str | None,
        token: str | None,
        default_branch: str = "main",
    ):
        self.cfg, self.ctx, self.root, self.repo, self.token = cfg, ctx, root, repo, token
        self.default_branch = default_branch
        self.lines: list[str] = []  # what goes to the job summary
        self.issues: dict[str, issues.Issue] | None = None  # None: not read
        self.history: History | None = None  # on main only
        self.recorded: dict[str, bool] = {}  # this run's outcome per test: failed
        self.laptop = ctx.spec.capped
        env = os.environ
        ref = env.get("GITHUB_REF", "")
        ci = ctx.ci and not self.laptop
        # Main is the push to the default branch: it writes the history and the bodies.
        push = env.get("GITHUB_EVENT_NAME") == "push"
        self.main = ci and push and ref == f"refs/heads/{default_branch}"
        # Anywhere else in CI a flake adds an occurrence: "#7" on a pull request, else the ref.
        m = re.fullmatch(r"refs/pull/(\d+)/merge", ref)
        where = f"#{m.group(1)}" if m else env.get("GITHUB_REF_NAME") or ref
        self.where = where if ci and not self.main else None
        self.sha = env.get("GITHUB_SHA", "")
        self.head = (_head_sha() if m else None) or self.sha  # the merge commit is no reader's
        self.run = env.get("GITHUB_RUN_ID", "")

    def _say(self, line: str) -> None:
        say(line)
        self.lines.append(line)

    def prepare(self, env: dict[str, str]) -> None:
        """Write the flagged file and set the plugin's variables in ``env``."""
        env["FASTCI_FLAKY_MARK"] = self.cfg.mark
        if self.cfg.flagged == "skip" and self.ctx.spec.tests == "picked":
            env["FASTCI_SKIP_FLAGGED"] = "1"
        if self.cfg.store in ("issues", "both"):
            flagged = self._flagged()
            if flagged is not None:
                path = os.path.join(self.root, FLAGGED)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(flagged, f, ensure_ascii=False)
                env["FASTCI_FLAGGED"] = path
        if self.main:
            self.history = self._fetch_history()

    def _flagged(self) -> dict[str, int] | None:
        """The open flaky issues, by node id; on a laptop offline, its last copy."""
        if self.repo and self.token:
            try:
                # On a laptop a dead network must not hold up the tests.
                timeout = 5 if self.laptop else 60
                self.issues = issues.listed(self.repo, self.token, self.cfg.label, timeout)
            except Exception as e:
                self._say(f"could not read the flaky issues: {e}")
        elif not self.laptop:
            self._say("could not read the flaky issues: no GitHub repository or token")
        if self.issues is not None:
            return {id: i.number for id, i in self.issues.items() if i.open}
        if not self.laptop:
            return None
        try:
            with open(os.path.join(self.root, FLAGGED), encoding="utf-8") as f:
                copy = json.load(f)
        except (OSError, ValueError):
            return None
        return copy if isinstance(copy, dict) else None

    def _fetch_history(self) -> History | None:
        """The newest history main uploaded; empty when there is none. None when one
        may exist but cannot be read: this run writes none, so it replaces nothing."""
        if not (self.repo and self.token):
            why = "no GitHub repository or token"
        else:
            try:
                art = fetch.newest_artifact(
                    self.repo, "fastci-flaky", self.default_branch, self.token
                )
                if art is None:
                    return History()
                with tempfile.TemporaryDirectory(prefix="fastci-") as tmp:
                    path = os.path.join(tmp, "flaky.json")
                    fetch._download(art["archive_download_url"], self.token, path, "flaky.json")
                    return History.read(path)
            except Exception as e:
                why = str(e) or type(e).__name__
        self._say(f"could not fetch the flaky history ({why}); this run does not write it")
        return None

    def after(
        self, verdicts: list[Verdict], res: results.Results, reach: Reach
    ) -> dict[str, list[str]]:
        """Flag each test the verdicts forgave; on main, add this run to the history.
        Returns the lines that close each forgiven test's verdict block."""
        flaky = [v.id for v in verdicts if v.kind == "flaky"]
        changed = self._record(flaky, res) if self.history is not None else set()
        notes: dict[str, list[str]] = {}
        for id in flaky:
            notes[id] = []
            if self.laptop:
                notes[id].append(
                    "failed and then passed; CI will flag it if it does the same there"
                )
                continue
            number = None
            if self.cfg.store in ("issues", "both"):
                number, line = self._write(id)
                notes[id] += [line] if line else []
            if self.cfg.store in ("marks", "both"):
                n = f"#{number}" if number else "#?"
                line = f'add @pytest.mark.{self.cfg.mark}("{n}") to {id}'
                notes[id].append(line)
                self.lines.append(line)
        for id in sorted(changed):
            issue = (self.issues or {}).get(id)
            if issue is not None and issue.open and self.history is not None:
                try:
                    text = issues.body(id, self.history.tests[id], self.cfg.unflag_after)
                    issues.edit(self.repo, self.token, issue.number, text)
                except Exception as e:
                    self._say(f"could not write the flag for {id}: {e}")
        if self.history is not None:
            self.history.save(os.path.join(self.root, HISTORY))
        return notes

    def _record(self, flaky: list[str], res: results.Results) -> set[str]:
        """Add the flagged tests' outcomes and the flakes to the history: the
        flagged tests whose rate changed."""
        assert self.history is not None
        date = datetime.datetime.now(datetime.UTC).date().isoformat()
        changed = set()
        for id in res.flagged:
            if id in res.outcomes:
                before = self.history.rate(id)
                self._add(id, res.outcomes[id] == "failed", res, date)
                if self.history.rate(id) != before:
                    changed.add(id)
        for id in flaky:
            self._add(id, True, res, date)
        return changed

    def _add(self, id: str, failed: bool, res: results.Results, date: str) -> None:
        """One outcome per test per run, though two stages may run it: failed if it
        failed in either."""
        assert self.history is not None
        if id in self.recorded:
            if self.recorded[id] or not failed:
                return
            entry = self.history.tests[id]
            entry.main = entry.main[:-1]  # this run's pass becomes its failure
        self.history.record(id, failed, self._where(id, res, date))
        self.recorded[id] = failed

    def _where(self, id: str, res: results.Results, date: str) -> dict:
        return {"sha": self.sha, "run": self.run, "date": date, "error": res.errors.get(id, "")}

    def _write(self, id: str) -> tuple[int | None, str]:
        """Open, reopen, rewrite or comment on the test's issue: its number, or None, and
        the line for the verdict's block."""
        if self.issues is None:
            line = f"could not write the flag for {id}: the flaky issues could not be read"
            self.lines.append(line)
            return None, line
        issue = self.issues.get(id)
        if not (self.main or self.where):  # not in CI: nothing to write
            return (issue.number, "") if issue else (None, "")
        repo, token = self.repo, self.token
        try:
            entry = self.history.tests[id] if self.history is not None else Entry()
            text = issues.body(id, entry, self.cfg.unflag_after)
            if issue is None:
                number, how = issues.open_issue(repo, token, self.cfg.label, id, text), " (new)"
            else:
                number, how = issue.number, "" if issue.open else " (reopened)"
                if not issue.open:
                    issues.set_open(repo, token, number, True)
                if self.main and self.history is not None:
                    issues.edit(repo, token, number, text)
            self.issues[id] = issues.Issue(number, True)
            if self.history is not None:
                entry.issue = number
            if self.where:
                when = (
                    f"Failed and then passed on {self.where} at {self.head[:7]} (run {self.run})."
                )
                issues.comment(repo, token, number, when)
        except Exception as e:
            line = f"could not write the flag for {id}: {e}"
            self.lines.append(line)
            return issue.number if issue else None, line
        line = f"flagged as flaky: #{number}{how}"
        self.lines.append(f"{id}: {line}")
        return number, line


def _head_sha() -> str | None:
    """The pull request's head commit, from the event payload."""
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
            return json.load(f)["pull_request"]["head"]["sha"]
    except (KeyError, OSError, ValueError, TypeError):
        return None


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
