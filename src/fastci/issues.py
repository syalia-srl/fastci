"""Flaky-test issues on GitHub: one per test, found by the marker line in its body.

The marker is ``<!-- fastci:test=NODEID -->`` on a line of its own. Lookup matches
that line exactly, never the title: GitHub's title search is fuzzy, and node ids
with parameters collide in it. A node id goes into the marker with ``%`` written
``%25`` and ``>`` written ``%3E``, so no id (``test_x[a-->b]``) can close the
comment early, and every id reads back exactly.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from time import monotonic

from . import fetch
from .flaky import Entry

MARKER = "<!-- fastci:test={} -->"
_MARKER = re.compile(r"<!-- fastci:test=(.*) -->")
TITLE_MAX = 256  # GitHub refuses a longer title; the marker, not the title, finds the issue


@dataclass(frozen=True)
class Issue:
    number: int
    open: bool


def marker(id: str) -> str:
    return MARKER.format(id.replace("%", "%25").replace(">", "%3E"))


def _id_in(body: str | None) -> str | None:
    for line in (body or "").splitlines():
        m = _MARKER.fullmatch(line.strip())
        if m:
            return urllib.parse.unquote(m.group(1))
    return None


def _next(link: str | None) -> str | None:
    for part in (link or "").split(","):
        m = re.fullmatch(r'\s*<([^>]*)>;\s*rel="next"\s*', part)
        if m:
            return m.group(1)
    return None


def listed(
    repo: str, token: str, label: str, timeout: float = 60, total: float | None = None
) -> dict[str, Issue]:
    """Every issue with ``label``, open and closed, by the node id in its marker.
    Of two issues for one test, an open one wins. ``timeout`` bounds each read,
    ``total`` all of them: past it, TimeoutError."""
    url = (
        f"{fetch.API}/repos/{repo}/issues?labels={urllib.parse.quote(label)}&state=all&per_page=100"
    )
    found: dict[str, Issue] = {}
    deadline = None if total is None else monotonic() + total
    while url:
        left = timeout if deadline is None else min(timeout, deadline - monotonic())
        if left <= 0:
            raise TimeoutError(f"gave up after {total:g} seconds")
        with fetch._get(url, token, timeout=left) as r:
            items = json.load(r)
            url = _next(r.headers.get("Link"))
        for it in items:
            id = None if "pull_request" in it else _id_in(it.get("body"))
            if id is not None and not (id in found and found[id].open):
                found[id] = Issue(it["number"], it["state"] == "open")
    return found


def open_issue(repo: str, token: str, label: str, id: str, body: str) -> int:
    issue = {"title": f"flaky: {id}"[:TITLE_MAX], "body": body, "labels": [label]}
    return fetch._send("POST", f"{fetch.API}/repos/{repo}/issues", token, issue)["number"]


def edit(repo: str, token: str, number: int, body: str) -> None:
    fetch._send("PATCH", f"{fetch.API}/repos/{repo}/issues/{number}", token, {"body": body})


def comment(repo: str, token: str, number: int, text: str) -> None:
    url = f"{fetch.API}/repos/{repo}/issues/{number}/comments"
    fetch._send("POST", url, token, {"body": text})


def set_open(repo: str, token: str, number: int, open: bool) -> None:
    state = {"state": "open" if open else "closed"}
    fetch._send("PATCH", f"{fetch.API}/repos/{repo}/issues/{number}", token, state)


def _seen(what: str, where: dict | None) -> list[str]:
    if not where:
        return []
    head = f"{what}: {where['sha'][:7]} (run {where['run']}), {where['date']}"
    return [head, *(f"  {line}" for line in (where.get("error") or "").splitlines())]


def body(id: str, entry: Entry, unflag_after: int) -> str:
    """The issue's body, rendered from the test's history; it replaces the old one."""
    failed, runs = sum(entry.main), len(entry.main)
    where = f"main, last {runs}" if runs else "main"
    rate = f"{round(100 * failed / runs)}%" if runs else "-"
    if unflag_after:
        leaves = [
            "It no longer blocks a run. It leaves the list once a change that reaches it",
            "has landed on main and main has run it "
            f"{unflag_after} times in a row without a failure.",
        ]
    else:
        leaves = ["It no longer blocks a run. It leaves the list when its issue is closed."]
    lines = [
        marker(id),
        "",
        "Failed and then passed on retry; passed in a fresh process: "
        "it may depend on test order. fastci keeps this issue up to date.",
        "",
        "| where | runs | failed | rate |",
        "|---|---:|---:|---:|",
        f"| {where} | {runs} | {failed} | {rate} |",
        "",
        "Each time it fails and then passes on a pull request, fastci adds a comment.",
        "",
        *_seen("First seen", entry.first),
        *_seen("Last failure", entry.last_failure),
        *([""] if entry.first or entry.last_failure else []),
        *leaves,
    ]
    return "\n".join(lines) + "\n"
