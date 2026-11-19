import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "sample"


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


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
