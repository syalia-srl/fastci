"""What the core needs from a language: its projects, their checks, and running their tests.

Each language is an adapter. The Python one runs in-process; adapters for other
languages will speak the same calls as JSON over stdin and stdout.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Check:
    name: str  # "lock" | "format" | "lint" | "types" | a config stage name
    run: str  # shell command
    fix: str | None  # the rewriting command for `fastci fix`, or None
    applies_to: tuple[str, ...]  # globs; skipped when the tier diffs and no changed file matches


@dataclass(frozen=True)
class Project:
    adapter: str  # "python"
    root: str  # relative to the repository root, "." for the root
    tools: frozenset[str]  # e.g. {"uv", "pytest", "xdist", "ruff", "ty"}
    checks: tuple[Check, ...]


class Adapter(Protocol):
    name: str

    def detect(self, repo: str) -> list[Project]: ...

    def run_tests(
        self,
        project: Project,
        repo: str,
        select: list[str] | None,
        args: list[str],
        env: dict[str, str],
        wrap: Callable[[list[str]], list[str]] = ...,
    ) -> int: ...

    def record(self, project: Project, repo: str, out: str, args: list[str]) -> int: ...

    def tests(self, project: Project, repo: str) -> list[tuple[str, frozenset[str]]]: ...


def adapters() -> list[Adapter]:
    from .python.adapter import PythonAdapter

    return [PythonAdapter()]
