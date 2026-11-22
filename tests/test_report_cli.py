"""The report command, its text and JSON, and how it finds maps."""

import json
import re

import pytest
from conftest import run_cli, sh

from fastci import fetch

TITLES = (
    "Expensive set-up",
    "Slowest tests",
    "Much time, little code",
    "Time by test file",
    "Same code path",
    "Almost the same",
    "Start-up path",
    "Coverage by file",
    "Never imported",
    "Flaky on main",
)
ID = r"tests/[\w/]+\.py::[\w\[\]-]+"


def test_report_prints_every_section_from_a_local_map(health_map):
    repo, _ = health_map
    code, out, err = run_cli(repo, "report", "--map", ".fastci/map.json.gz")
    assert code == 0, err
    for title in TITLES:
        assert title in out
    assert "tests/test_same.py::test_a" in out


def test_json_and_text_name_the_same_tests(health_map):
    repo, _ = health_map
    _, text, _ = run_cli(repo, "report", "--map", ".fastci/map.json.gz", "--top", "1000")
    code, js, err = run_cli(repo, "report", "--map", ".fastci/map.json.gz", "--json")
    assert code == 0, err
    assert json.loads(js)["version"] == 1
    ids = set(re.findall(ID, js))
    assert ids and ids <= set(re.findall(ID, text))


def test_no_map_and_no_token_exits_2_with_how_to_get_one(sample):
    code, _, err = run_cli(sample, "report")
    assert code == 2 and "fastci map record" in err


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/tool.git",
        "https://github.com/acme/tool",
        "https://github.com/acme/tool.git",
    ],
)
def test_repo_from_remote(tmp_path, url):
    sh(tmp_path, "git", "init", "-q")
    sh(tmp_path, "git", "remote", "add", "origin", url)
    assert fetch.repo_from_remote(str(tmp_path)) == "acme/tool"


def test_newest_artifacts_keeps_the_branchs_unexpired_newest_first(monkeypatch):
    arts = [
        {
            "id": 1,
            "expired": False,
            "created_at": "2026-01-01",
            "workflow_run": {"head_branch": "main"},
        },
        {
            "id": 2,
            "expired": True,
            "created_at": "2026-01-03",
            "workflow_run": {"head_branch": "main"},
        },
        {
            "id": 3,
            "expired": False,
            "created_at": "2026-01-02",
            "workflow_run": {"head_branch": "main"},
        },
        {
            "id": 4,
            "expired": False,
            "created_at": "2026-01-04",
            "workflow_run": {"head_branch": "dev"},
        },
    ]

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"artifacts": arts}).encode()

    monkeypatch.setattr(fetch, "_get", lambda *a, **k: Answer())
    got = fetch.newest_artifacts("o/r", "fastci", "main", "t", 5)
    assert [a["id"] for a in got] == [3, 1]
    assert fetch.newest_artifacts("o/r", "fastci", "main", "t", 1)[0]["id"] == 3
