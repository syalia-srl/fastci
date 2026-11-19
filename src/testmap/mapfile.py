"""The map: which tests ran which lines and functions, and which tests read which files.

One gzipped JSON file. Tests are stored once, in ``tests``, and referred to by
index everywhere else. Every covered line keeps a short hash of its text, so a
pull request whose base is a few commits away from the map's commit can still
find the same line by its text inside the same function.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
from dataclasses import dataclass, field

VERSION = 1


def line_hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:10]


def functions(source: str) -> list[tuple[str, int, int]]:
    """(qualified name, first line, last line) of every function, decorators included."""
    out: list[tuple[str, int, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                if not isinstance(child, ast.ClassDef):
                    out.append((name, start, child.end_lineno or child.lineno))
                walk(child, name + ".")

    walk(tree, "")
    return out


def enclosing(funcs: list[tuple[str, int, int]], line: int) -> str | None:
    """The innermost function holding the line, or None at module level."""
    best = None
    for name, start, end in funcs:
        if start <= line <= end and (best is None or start >= best[1]):
            best = (name, start, end)
    return best[0] if best else None


def docstring_lines(source: str) -> set[int]:
    out: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            out.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    return out


@dataclass
class FileEntry:
    tests: set[int] = field(default_factory=set)
    funcs: dict[str, tuple[int, int, set[int]]] = field(default_factory=dict)
    lines: dict[int, tuple[str, set[int]]] = field(default_factory=dict)


@dataclass
class TestMap:
    sha: str
    tests: list[str]
    durations: list[float]
    always: set[int]
    files: dict[str, FileEntry]
    reads: dict[str, set[int]]

    def ids(self, idx) -> set[str]:
        return {self.tests[i] for i in idx}

    def save(self, path: str) -> None:
        data = {
            "version": VERSION,
            "sha": self.sha,
            "tests": self.tests,
            "durations": [round(d, 3) for d in self.durations],
            "always": sorted(self.always),
            "files": {
                p: {
                    "tests": sorted(e.tests),
                    "funcs": {q: [s, t, sorted(i)] for q, (s, t, i) in e.funcs.items()},
                    "lines": {str(n): [h, sorted(i)] for n, (h, i) in e.lines.items()},
                }
                for p, e in self.files.items()
            },
            "reads": {p: sorted(i) for p, i in self.reads.items()},
        }
        with gzip.open(path, "wt") as f:
            json.dump(data, f, separators=(",", ":"))

    @classmethod
    def load(cls, path: str) -> TestMap:
        with gzip.open(path, "rt") as f:
            data = json.load(f)
        if data.get("version") != VERSION:
            raise ValueError(f"map version {data.get('version')} is not {VERSION}")
        files = {
            p: FileEntry(
                tests=set(e["tests"]),
                funcs={q: (s, t, set(i)) for q, (s, t, i) in e["funcs"].items()},
                lines={int(n): (h, set(i)) for n, (h, i) in e["lines"].items()},
            )
            for p, e in data["files"].items()
        }
        return cls(
            sha=data["sha"],
            tests=data["tests"],
            durations=data["durations"],
            always=set(data["always"]),
            files=files,
            reads={p: set(i) for p, i in data["reads"].items()},
        )
