"""``fastci map fetch``: download the newest map that a branch's CI uploaded.

It reads the GitHub REST API with the token in ``GH_TOKEN`` or
``GITHUB_TOKEN``, which needs ``actions: read``. Standard library only, so it
runs on a runner without the ``gh`` CLI.
"""

from __future__ import annotations

import io
import json
import os
import urllib.parse
import urllib.request
import zipfile

API = "https://api.github.com"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get(url: str, token: str, follow: bool = True, timeout: float = 60):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if follow:
        return urllib.request.urlopen(req, timeout=timeout)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return e
        raise


def newest_artifact(
    repo: str, name: str, branch: str, token: str, timeout: float = 60
) -> dict | None:
    url = f"{API}/repos/{repo}/actions/artifacts?name={name}&per_page=100"
    with _get(url, token, timeout=timeout) as r:
        arts = json.load(r)["artifacts"]
    arts = [
        a
        for a in arts
        if not a["expired"] and (a.get("workflow_run") or {}).get("head_branch") == branch
    ]
    return max(arts, key=lambda a: a["created_at"]) if arts else None


def green_shas(
    repo: str,
    branch: str,
    workflow: str,
    token: str,
    exclude_run: str | None,
    timeout: float = 60,
) -> list[str]:
    """The commits of the successful push runs of ``workflow`` (its file name) on
    ``branch``, newest first, leaving out the run ``exclude_run`` (the one asking)."""
    url = (
        f"{API}/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow)}/runs"
        f"?branch={urllib.parse.quote(branch)}&event=push&status=success&per_page=50"
    )
    with _get(url, token, timeout=timeout) as r:
        runs = json.load(r)["workflow_runs"]
    return [run["head_sha"] for run in runs if str(run["id"]) != exclude_run]


def _download(url: str, token: str, out: str) -> None:
    first = _get(url, token, follow=False)
    location = first.headers.get("Location")
    data = urllib.request.urlopen(location, timeout=120).read() if location else first.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        member = next(n for n in z.namelist() if n.endswith(".json.gz"))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "wb") as f:
            f.write(z.read(member))


def fetch(repo: str, out: str, branch: str = "main", name: str = "fastci") -> str | None:
    """Write the newest map to ``out``; return the commit it was recorded at, or None.

    Maps uploaded before the rename are named ``testmap``; they are used when no
    ``fastci`` artifact exists.
    """
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("fastci: map fetch needs GH_TOKEN or GITHUB_TOKEN")
    art = newest_artifact(repo, name, branch, token)
    if art is None and name == "fastci":
        art = newest_artifact(repo, "testmap", branch, token)
    if art is None:
        return None
    _download(art["archive_download_url"], token, out)
    return art["workflow_run"].get("head_sha")
