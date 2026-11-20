"""The Python adapter: finds a pytest project's tools and runs its tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from ..adapter import Check, Project

PY = ("**/*.py", "*.py", "pyproject.toml")
OWN_VARS = ("FASTCI_SELECT", "FASTCI_RECORD", "FASTCI_COLLECT", "FASTCI_CTX")
# The directory holding the fastci package, so a project's own Python can import
# the plugin without fastci being installed in that project's environment.
FASTCI_PATH = str(Path(__file__).resolve().parents[2])


def _names(specs) -> set[str]:
    out = set()
    for s in specs:
        if isinstance(s, str):
            out.add(re.split(r"[<>=!~;\[ ]", s.strip(), maxsplit=1)[0].lower())
    return out


def _deps(pp: dict) -> set[str]:
    project = pp.get("project", {})
    names = _names(project.get("dependencies", []))
    for group in (pp.get("dependency-groups", {}), project.get("optional-dependencies", {})):
        for specs in group.values():
            names |= _names(specs)
    names |= _names(pp.get("tool", {}).get("uv", {}).get("dev-dependencies", []))
    return names


def _tools(root: Path, pp: dict) -> frozenset[str]:
    deps, tool = _deps(pp), pp.get("tool", {})
    has = lambda *files: any((root / f).exists() for f in files)  # noqa: E731
    found = set()
    if has("uv.lock"):
        found.add("uv")
    if "pytest" in deps or "pytest" in tool or has("pytest.ini", "conftest.py"):
        found.add("pytest")
    if "pytest-xdist" in deps:
        found.add("xdist")
    if "ruff" in deps or "ruff" in tool or has("ruff.toml", ".ruff.toml"):
        found.add("ruff")
    if "ty" in deps or "ty" in tool:
        found.add("ty")
    if "mypy" in deps or "mypy" in tool or has("mypy.ini"):
        found.add("mypy")
    if "pyright" in deps or "pyright" in tool or has("pyrightconfig.json"):
        found.add("pyright")
    return frozenset(found)


def _checks(tools: frozenset[str]) -> tuple[Check, ...]:
    run = "uv run " if "uv" in tools else ""
    out = []
    if "uv" in tools:
        out.append(Check("lock", "uv lock --check", None, ("pyproject.toml", "uv.lock")))
    if "ruff" in tools:
        out.append(Check("format", f"{run}ruff format --check", f"{run}ruff format", PY))
        out.append(Check("lint", f"{run}ruff check", f"{run}ruff check --fix", PY))
    for name, cmd in (("ty", "ty check"), ("mypy", "mypy ."), ("pyright", "pyright")):
        if name in tools:
            out.append(Check("types", f"{run}{cmd}", None, PY))
            break
    return tuple(out)


class PythonAdapter:
    name = "python"

    def __init__(self, python: list[str] | None = None):
        self._python = python

    def python(self, project: Project) -> list[str]:
        if self._python:
            return list(self._python)
        if "uv" in project.tools:
            return ["uv", "run", "python"]
        # Many systems have only python3; an active virtualenv puts python first on PATH.
        return ["python"] if shutil.which("python") else ["python3"]

    def detect(self, repo: str) -> list[Project]:
        root = Path(repo)
        if not (root / "pyproject.toml").exists() and not (root / "setup.py").exists():
            return []
        pp = {}
        if (root / "pyproject.toml").exists():
            pp = tomllib.loads((root / "pyproject.toml").read_text())
        tools = _tools(root, pp)
        return [Project("python", ".", tools, _checks(tools))]

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        # Variables an outer fastci run set are not this run's.
        env = {k: v for k, v in os.environ.items() if k not in OWN_VARS}
        env.update(extra or {})
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [FASTCI_PATH, env.get("PYTHONPATH")]))
        return env

    def run_tests(self, project, repo, select, args, env, wrap=lambda a: a) -> int:
        extra = dict(env)
        if select is not None:
            fd, path = tempfile.mkstemp(prefix="fastci-select-", suffix=".txt")
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(select) + "\n")
            extra["FASTCI_SELECT"] = path
        cmd = [*self.python(project), "-m", "pytest", "-p", "fastci.python.plugin", *args]
        code = subprocess.run(wrap(cmd), cwd=os.path.join(repo, project.root), env=self.env(extra))
        return code.returncode

    def record(self, project, repo, out, args, sources=None) -> int:
        from .record import default_sources, record

        root = os.path.join(repo, project.root)
        python = self.python(project)
        if python[:2] == ["uv", "run"]:
            python = ["uv", "run", "--with", "pytest-cov", *python[2:]]
        sources = sources or default_sources(root)
        return record(args, out, sources, root, python=python, env=self.env())

    def tests(self, project, repo) -> list[tuple[str, frozenset[str]]]:
        fd, path = tempfile.mkstemp(prefix="fastci-collect-", suffix=".jsonl")
        os.close(fd)
        cmd = [*self.python(project), "-m", "pytest", "-p", "fastci.python.plugin"]
        subprocess.run(
            [*cmd, "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=os.path.join(repo, project.root),
            env=self.env({"FASTCI_COLLECT": path}),
            capture_output=True,
        )
        with open(path) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        os.unlink(path)
        return [(r["id"], frozenset(r["marks"])) for r in rows]
