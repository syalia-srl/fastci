import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

FIXTURE = Path(__file__).parent / "fixtures" / "sample"
HEALTH = Path(__file__).parent / "fixtures" / "health"


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture(autouse=True)
def _no_outer_fastci_run(monkeypatch):
    """When fastci runs this suite, its own variables must not reach the runs tests start,
    nor the CI's: with GITHUB_REPOSITORY and a token a test would fetch this repo's map."""
    for k in ("FASTCI_SELECT", "FASTCI_RECORD", "FASTCI_COLLECT", "FASTCI_CTX", "FASTCI_RESULTS"):
        monkeypatch.delenv(k, raising=False)
    github = ("GITHUB_REPOSITORY", "GH_TOKEN", "GITHUB_TOKEN")
    github += ("GITHUB_EVENT_NAME", "GITHUB_REF", "GITHUB_BASE_REF")
    github += ("GITHUB_EVENT_PATH", "GITHUB_SHA")
    for k in github:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def sample(tmp_path):
    """A git repository holding the sample project, with one commit."""
    repo = tmp_path / "sample"
    shutil.copytree(FIXTURE, repo)
    sh(repo, "git", "init", "-q", "-b", "main")
    sh(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    sh(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "start")
    return repo


def run_testmap(repo, *args):
    """Run the fastci map CLI inside the repo; return (exit code, stdout, stderr)."""
    args = ("map", *args)
    env = dict(os.environ)
    env.pop("FASTCI_SELECT", None)
    env.pop("FASTCI_RECORD", None)
    p = subprocess.run(
        [sys.executable, "-m", "fastci.cli", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    return p.returncode, p.stdout, p.stderr


def commit(repo, msg="change"):
    sh(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", msg)


@pytest.fixture(scope="session")
def health_map(tmp_path_factory):
    """The health sample recorded once: (repo, its map). Recording costs about 2 s, and the
    report's tests only read it."""
    from fastci.python.mapfile import TestMap

    repo = tmp_path_factory.mktemp("health") / "health"
    shutil.copytree(HEALTH, repo)
    sh(repo, "git", "init", "-q", "-b", "main")
    sh(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    sh(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "start")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FASTCI_", "GITHUB_", "GH_"))}
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastci.cli",
            "map",
            "record",
            "--",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            "not slow",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    assert (repo / ".fastci" / "map.json.gz").exists(), p.stderr + p.stdout
    return repo, TestMap.load(str(repo / ".fastci" / "map.json.gz"))
