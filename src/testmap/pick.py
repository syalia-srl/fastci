"""``testmap pick``: the tests a diff can break, according to a map.

The rules, in order, for every path the diff touches:

1. A path that matches a ``full`` pattern (lockfile, pyproject, root conftest,
   CI workflows, ...) means every test.
2. A test file means its tests; any other file under a tests folder means the
   tests whose source names it.
3. A Python line in a function means the tests that ran that line, found by
   its text inside the same function of the map; when the line is new, the
   tests that ran the function.
4. A Python line at module level means the tests that ran any line of the file,
   except an import, which means only the fastest of them (it proves the
   module still imports). Blank lines, comments and docstrings mean nothing.
5. Any file a test opened, Python or not, means the tests that opened it, plus
   the tests whose source names the file.
6. Tests marked ``always``, and the files listed in ``always``, always run.

An optional ``global_share`` narrows a unit that more than that share of the
tests ran (a server's start-up path, say) to the tests that name it plus the
five fastest. It is off by default because it trades recall for time.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .mapfile import TestMap, docstring_lines, enclosing, functions, line_hash

DEFAULT_FULL = [
    "uv.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pytest.ini",
    "tox.ini",
    "requirements*.txt",
    "Makefile",
    "conftest.py",
    "tests/conftest.py",
    ".github/workflows/*",
]
IMPORT = re.compile(r"^(from\s+\S+\s+import\b|import\s|\)$|[\w\s,]+,?\)?$)")


@dataclass
class Config:
    full: list[str] = field(default_factory=lambda: list(DEFAULT_FULL))
    always: list[str] = field(default_factory=list)
    global_share: float = 1.0

    @classmethod
    def load(cls, root: str) -> Config:
        path = Path(root) / "pyproject.toml"
        cfg = cls()
        if path.exists():
            t = tomllib.loads(path.read_text()).get("tool", {}).get("testmap", {})
            cfg.full += t.get("full", [])
            cfg.always = t.get("always", [])
            cfg.global_share = float(t.get("global_share", 1.0))
        return cfg


@dataclass
class Pick:
    full: bool
    tests: set[str]
    files: set[str]
    why: dict[str, str]

    def lines(self) -> list[str]:
        return sorted(self.tests | self.files)


def git(root: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, errors="ignore"
    ).stdout


def changed(root: str, base: str, head: str | None) -> dict[str, tuple[set[int], set[int]]]:
    """{path: (lines on the head side, lines on the base side)} from a zero-context diff."""
    args = ["diff", "-U0", "--no-renames", base] + ([head] if head else [])
    out: dict[str, tuple[set[int], set[int]]] = {}
    old = new = None
    for line in git(root, *args).splitlines():
        if line.startswith("--- "):
            old = None if line == "--- /dev/null" else line[6:]
        elif line.startswith("+++ "):
            new = None if line == "+++ /dev/null" else line[6:]
            out.setdefault(new or old, (set(), set()))
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m:
                continue
            os_, oc, ns, nc = int(m[1]), int(m[2] or 1), int(m[3]), int(m[4] or 1)
            head_lines, base_lines = out[new or old]
            head_lines.update(range(ns, ns + nc))
            base_lines.update(range(os_, os_ + oc))
    return out


def is_test_file(path: str) -> bool:
    name = os.path.basename(path)
    return path.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def in_tests_dir(path: str) -> bool:
    return "tests/" in path or path.startswith("test/")


class Picker:
    def __init__(self, tm: TestMap, root: str, config: Config):
        self.tm, self.root, self.cfg = tm, root, config
        self._sources: dict[str, str] | None = None
        self.by_file: dict[str, set[str]] = {}
        for t in tm.tests:
            self.by_file.setdefault(t.split("::")[0], set()).add(t)

    def test_sources(self) -> dict[str, str]:
        if self._sources is None:
            self._sources = {}
            for f in self.by_file:
                try:
                    self._sources[f] = (Path(self.root) / f).read_text(errors="ignore")
                except OSError:
                    pass
        return self._sources

    def naming(self, names: set[str]) -> set[str]:
        names = {n for n in names if n and len(n) >= 4}
        out: set[str] = set()
        for f, src in self.test_sources().items():
            if any(n in src for n in names):
                out |= self.by_file[f]
        return out

    def narrow(self, idx: set[int], names: set[str]) -> set[str]:
        tests = self.tm.ids(idx)
        if len(tests) <= self.cfg.global_share * len(self.tm.tests):
            return tests
        fastest = sorted(idx, key=lambda i: self.tm.durations[i])[:5]
        return self.tm.ids(fastest) | (self.naming(names) & tests)

    def fastest(self, idx: set[int]) -> set[str]:
        return self.tm.ids(sorted(idx, key=lambda i: self.tm.durations[i])[:1]) if idx else set()

    def pick(self, base: str, head: str | None = None) -> Pick:
        diff = changed(self.root, base, head)
        why: dict[str, str] = {}
        tests: set[str] = set()
        files: set[str] = set()

        def add(ts: set[str], reason: str) -> None:
            for t in ts:
                why.setdefault(t, reason)
            tests.update(ts)

        for path in diff:
            if any(fnmatch.fnmatch(path, p) for p in self.cfg.full):
                return Pick(True, set(self.tm.tests), set(), {"*": f"{path} changed"})

        for path, (head_lines, base_lines) in diff.items():
            if is_test_file(path):
                files.add(path)
                why[path] = "test file changed"
                continue
            if in_tests_dir(path):
                stem = os.path.splitext(os.path.basename(path))[0]
                add(self.naming({os.path.basename(path), stem}), f"{path} (test helper) named")
                continue
            if path in self.tm.reads:
                add(self.narrow(self.tm.reads[path], {os.path.basename(path)}), f"{path} read")
            if not path.endswith(".py"):
                add(
                    self.naming({os.path.basename(path), "/".join(path.split("/")[-2:])}),
                    f"{path} named",
                )
                continue
            entry = self.tm.files.get(path)
            if entry is None:
                stem = os.path.splitext(os.path.basename(path))[0]
                add(self.naming({stem}), f"{path} is new to the map; tests naming it")
                continue
            for rev, lines in ((head, head_lines), (base, base_lines)):
                src = self.show(rev, path)
                text = src.splitlines()
                funcs = functions(src)
                docs = docstring_lines(src)
                for n in sorted(lines):
                    body = text[n - 1].strip() if 0 < n <= len(text) else ""
                    if not body or body.startswith("#") or n in docs:
                        continue
                    q = enclosing(funcs, n)
                    if q and q in entry.funcs:
                        add(
                            self.narrow(self.line_tests(entry, q, body), {q.split(".")[-1]}),
                            f"{path}:{q}",
                        )
                    elif q is None and IMPORT.match(body):
                        add(self.fastest(entry.tests), f"{path} imports")
                    else:
                        stem = os.path.splitext(os.path.basename(path))[0]
                        add(self.narrow(entry.tests, {stem}), f"{path} module level or new code")

        for i in self.tm.always:
            add({self.tm.tests[i]}, "marked always")
        for f in self.cfg.always:
            files.add(f)
            why.setdefault(f, "listed in always")
        return Pick(False, tests, files, why)

    def line_tests(self, entry, q: str, body: str) -> set[int]:
        start, end, func_tests = entry.funcs[q]
        h = line_hash(body)
        hits = [n for n in range(start, end + 1) if n in entry.lines and entry.lines[n][0] == h]
        return entry.lines[hits[0]][1] if len(hits) == 1 else func_tests

    def show(self, rev: str | None, path: str) -> str:
        if rev is None:
            try:
                return (Path(self.root) / path).read_text(errors="ignore")
            except OSError:
                return ""
        return git(self.root, "show", f"{rev}:{path}")
