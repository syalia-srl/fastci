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


def test_green_shas_asks_for_this_workflows_runs_and_skips_this_run(monkeypatch):
    runs = [
        {"id": 9, "head_sha": "this"},
        {"id": 7, "head_sha": "green"},
        {"id": 6, "head_sha": "older"},
    ]
    seen = []
    _runs(monkeypatch, runs, seen)
    assert fetch.green_shas("o/r", "main", "fastci.yml", "t", "9") == ["green", "older"]
    assert seen[0].startswith(f"{fetch.API}/repos/o/r/actions/workflows/fastci.yml/runs?")
    assert "branch=main" in seen[0] and "status=success" in seen[0] and "event=push" in seen[0]


def test_green_shas_empty_without_runs(monkeypatch):
    _runs(monkeypatch, [])
    assert fetch.green_shas("o/r", "main", "fastci.yml", "t", None) == []


def test_download_extracts_the_member_with_the_suffix(monkeypatch, tmp_path):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("map.json.gz", b"map")
        z.writestr("flaky.json", b'{"version": 1}')

    class R(io.BytesIO):
        headers = {}

    monkeypatch.setattr(fetch, "_get", lambda url, token, follow=True: R(buf.getvalue()))
    out = tmp_path / "x" / "flaky.json"
    fetch._download("u", "t", str(out), suffix=".json")
    assert out.read_bytes() == b'{"version": 1}'
    fetch._download("u", "t", str(tmp_path / "map.gz"))
    assert (tmp_path / "map.gz").read_bytes() == b"map"
