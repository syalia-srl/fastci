"""fetch picks the newest unexpired artifact uploaded from the branch."""

import io
import json

from testmap import fetch


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

    monkeypatch.setattr(fetch, "_get", lambda url, token, follow=True: R(json.dumps(arts).encode()))
    assert fetch.newest_artifact("o/r", "testmap", "main", "t")["id"] == 2
