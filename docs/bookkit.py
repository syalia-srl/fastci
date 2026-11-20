"""What the book's hidden blocks use: a small project in a temporary git repository.

Every command runs the fastci installed next to the book, with the CI's own
variables removed, so a page reads the same on a laptop and in CI.
"""

import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

FILES = {
    "pyproject.toml": """\
        [project]
        name = "shop"
        version = "0.1.0"
        requires-python = ">=3.11"

        [dependency-groups]
        dev = ["pytest"]

        [tool.pytest.ini_options]
        pythonpath = ["src"]
        testpaths = ["tests"]
        """,
    ".gitignore": ".venv/\n.fastci/\n",
    "src/shop/__init__.py": "",
    "src/shop/cart.py": """\
        import json
        from pathlib import Path

        PRICES = Path(__file__).parent / "prices.json"


        def total(items):
            prices = json.loads(PRICES.read_text())
            return sum(prices[i] for i in items)


        def discount(price, pct):
            return price * (1 - pct / 100)
        """,
    "src/shop/prices.json": '{"apple": 2, "bread": 3}\n',
    "tests/test_total.py": """\
        from shop.cart import total


        def test_total():
            assert total(["apple", "bread"]) == 5
        """,
    "tests/test_discount.py": """\
        from shop.cart import discount


        def test_discount():
            assert discount(100, 10) == 90
        """,
}
CLEAN = {k: v for k, v in os.environ.items() if k != "CI" and not k.startswith("GITHUB_")}
CLEAN.pop("VIRTUAL_ENV", None)
CLEAN["UV_LINK_MODE"] = "copy"


class Demo:
    """The shop project, committed, with origin/main at its first commit."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="fastci-book-")) / "shop"
        for path, text in FILES.items():
            self.write(path, text)
        self._run("uv", "sync", "-q")
        self._run("git", "init", "-q", "-b", "main")
        self.commit("start", merge=True)

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))

    def edit(self, path, old, new):
        p = self.root / path
        p.write_text(p.read_text().replace(old, new))

    def commit(self, msg, merge=False):
        """Commit everything; with merge, also move origin/main here, as a merge would."""
        self._run("git", "add", "-A")
        self._run(
            "git", "-c", "user.email=book@fastci", "-c", "user.name=book", "commit", "-qm", msg
        )
        if merge:
            self._run("git", "update-ref", "refs/remotes/origin/main", "HEAD")

    def _run(self, *cmd, env=None):
        return subprocess.run(cmd, cwd=self.root, env=env or CLEAN, capture_output=True, text=True)

    def sh(self, line, env=None):
        """Print a fastci command line and what it printed, stdout and stderr together."""
        argv = shlex.split(line)
        assert argv[0] == "fastci"
        out = subprocess.run(
            [sys.executable, "-m", "fastci.cli", *argv[1:]],
            cwd=self.root,
            env={**CLEAN, **(env or {})},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print(f"$ {line}\n{out.stdout.rstrip()}")
        if out.returncode:
            print(f"(exit {out.returncode})")

    def show(self, path):
        print((self.root / path).read_text().rstrip())
