"""fetch picks the newest unexpired artifact uploaded from the branch."""

import io
import json

from fastci import fetch


def test_newest_artifact_from_the_branch(monkeypatch):
    arts = {
        "artifacts": [
            {
                "expired": False,
                "created_at": "2026-11-01",
                "workflow_run": {"head_branch": "main"},
                "id": 1,
            },
            {
                "expired": False,
                "created_at": "2026-11-03",
                "workflow_run": {"head_branch": "main"},
                "id": 2,
            },
            {
                "expired": True,
                "created_at": "2026-11-05",
                "workflow_run": {"head_branch": "main"},
                "id": 3,
            },
            {
                "expired": False,
                "created_at": "2026-11-06",
                "workflow_run": {"head_branch": "feature"},
                "id": 4,
            },
        ]
    }

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        fetch, "_get", lambda url, token, follow=True, timeout=60: R(json.dumps(arts).encode())
    )
    assert fetch.newest_artifact("o/r", "testmap", "main", "t")["id"] == 2


def test_fetch_falls_back_to_testmap_artifact(monkeypatch, tmp_path):
    calls = []

    def fake_newest(repo, name, branch, token):
        calls.append(name)
        if name == "fastci":
            return None
        return {"id": 1, "workflow_run": {"head_sha": "abc"}, "archive_download_url": "u"}

    monkeypatch.setattr(fetch, "newest_artifact", fake_newest)
    monkeypatch.setattr(fetch, "_download", lambda url, token, out: None)
    monkeypatch.setenv("GH_TOKEN", "t")
    assert fetch.fetch("o/r", str(tmp_path / "m.gz")) == "abc"
    assert calls == ["fastci", "testmap"]


def _runs(monkeypatch, runs, seen=None):
    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def get(url, token, follow=True, timeout=60):
        if seen is not None:
            seen.append(url)
        return R(json.dumps({"workflow_runs": runs}).encode())

    monkeypatch.setattr(fetch, "_get", get)


def test_last_green_sha_skips_other_workflows_and_this_run(monkeypatch):
    runs = [
        {"id": 9, "path": ".github/workflows/fastci.yml", "head_sha": "this"},
        {"id": 8, "path": ".github/workflows/docs.yml", "head_sha": "docs"},
        {"id": 7, "path": ".github/workflows/fastci.yml", "head_sha": "green"},
        {"id": 6, "path": ".github/workflows/fastci.yml", "head_sha": "older"},
    ]
    seen = []
    _runs(monkeypatch, runs, seen)
    sha = fetch.last_green_sha("o/r", "main", ".github/workflows/fastci.yml", "t", "9")
    assert sha == "green"
    assert "branch=main" in seen[0] and "status=success" in seen[0] and "event=push" in seen[0]


def test_last_green_sha_none_without_runs(monkeypatch):
    _runs(monkeypatch, [])
    assert fetch.last_green_sha("o/r", "main", ".github/workflows/fastci.yml", "t", None) is None
