"""Tell whoever runs fastci, person or agent, that a newer release exists.

One lookup of the latest GitHub release, at most once a day on a laptop (cached
under ``$XDG_CACHE_HOME/fastci``) and on every run in GitHub Actions, where the
notice is also a warning annotation and a line in the job summary so that an agent
reading the run sees it. It never fails a run: no network, no answer, no notice.
``FASTCI_NO_VERSION_CHECK=1`` turns it off.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

REPO = "syalia-srl/fastci"
DAY = 86400


def _fetch_latest(token: str | None, timeout: float) -> str | None:
    import urllib.request

    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json"}
        | ({"Authorization": f"Bearer {token}"} if token else {}),
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("tag_name")


def _numbers(v: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", v.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def out_of_date(current: str, latest: str | None) -> bool:
    """Whether ``latest`` is a newer release than ``current``; never for a source tree."""
    cur, new = _numbers(current), _numbers(latest or "")
    return bool(cur and new and cur != (0, 0, 0) and new > cur)


def _cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "fastci" / "latest.json"


def _latest(token: str | None, ci: bool) -> str | None:
    path = _cache()
    if not ci:
        try:
            data = json.loads(path.read_text())
            if time.time() - data["checked"] < DAY:
                return data["tag"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        tag = _fetch_latest(token, 3)
    except Exception:
        tag = None
    if not ci:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"checked": time.time(), "tag": tag}))
        except OSError:
            pass
    return tag


def check(current: str, token: str | None) -> None:
    """Print the notice when a newer fastci exists; on GitHub, annotate the run too."""
    if os.environ.get("FASTCI_NO_VERSION_CHECK"):
        return
    ci = os.environ.get("GITHUB_ACTIONS") == "true"
    latest = _latest(token, ci)
    if not out_of_date(current, latest):
        return
    new = latest.lstrip("v")
    text = (
        f"fastci {current} is out of date; {new} is out. Update the pin to "
        f"git+https://github.com/{REPO}@v{new} wherever this repository installs fastci "
        f"(its CI workflows, its docs), and read what changed in "
        f"https://github.com/{REPO}/releases/tag/v{new}"
    )
    print(f"fastci: {text}", file=sys.stderr, flush=True)
    if ci:
        print(f"::warning title=fastci is out of date::{text}", flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"> [!WARNING]\n> {text}\n\n")
