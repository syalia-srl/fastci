"""fastci's configuration: ``[tool.fastci]`` in pyproject.toml, else fastci.toml.

``[tool.testmap]`` is still read when neither exists, so repositories that
adopted fastci under its old name keep working.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TIERS = frozenset({"T0", "T1", "T2", "T3"})


class ConfigError(ValueError):
    pass


@dataclass
class StageSpec:
    run: str  # a shell command; "" turns the stage off
    tiers: frozenset[str] = TIERS
    after: str | None = None  # name of the stage it runs after


@dataclass
class TestsConfig:
    __test__ = False

    args: list[str] = field(default_factory=list)
    full: list[str] = field(default_factory=list)  # added to the picker's DEFAULT_FULL
    always: list[str] = field(default_factory=list)
    release_only: list[str] = field(
        default_factory=lambda: ["slow", "gpu", "browser", "e2e", "bench"]
    )
    slow_seconds: float = 10.0
    global_share: float = 1.0


@dataclass
class LocalConfig:
    memory: float = 0.5  # share of MemAvailable
    workers: int | None = None  # None = auto
    fail_fast: bool = True


@dataclass
class Config:
    default_branch: str = "main"
    stages: dict[str, StageSpec] = field(default_factory=dict)
    tests: TestsConfig = field(default_factory=TestsConfig)
    local: LocalConfig = field(default_factory=LocalConfig)
    source: str = "defaults"  # "pyproject", "fastci.toml", "testmap" or "defaults"


def _check(table: dict, allowed: set[str], where: str) -> None:
    for key in table:
        if key not in allowed:
            raise ConfigError(f"unknown key '{key}' in {where}")


def _stage(name: str, value, where: str) -> StageSpec:
    if isinstance(value, str):
        return StageSpec(run=value)
    if not isinstance(value, dict):
        raise ConfigError(f"stage '{name}' in {where} must be a string or a table")
    _check(value, {"run", "tiers", "after"}, f"{where} stage '{name}'")
    tiers = frozenset(value.get("tiers", TIERS))
    if not tiers <= TIERS:
        raise ConfigError(f"stage '{name}' in {where} has tiers outside T0-T3: {sorted(tiers)}")
    return StageSpec(run=value.get("run", ""), tiers=tiers, after=value.get("after"))


def _percent(value, where: str) -> float:
    if isinstance(value, str) and value.endswith("%"):
        return float(value[:-1]) / 100
    raise ConfigError(f'memory in {where} must be a percentage such as "50%"')


def _parse(t: dict, where: str, source: str) -> Config:
    _check(t, {"default-branch", "stages", "tests", "local"}, where)
    cfg = Config(source=source, default_branch=t.get("default-branch", "main"))
    cfg.stages = {k: _stage(k, v, where) for k, v in t.get("stages", {}).items()}
    tests = t.get("tests", {})
    _check(
        tests,
        {"args", "full", "always", "release-only", "slow-seconds", "global-share"},
        f"{where} tests",
    )
    d = TestsConfig()
    cfg.tests = TestsConfig(
        args=list(tests.get("args", d.args)),
        full=list(tests.get("full", d.full)),
        always=list(tests.get("always", d.always)),
        release_only=list(tests.get("release-only", d.release_only)),
        slow_seconds=float(tests.get("slow-seconds", d.slow_seconds)),
        global_share=float(tests.get("global-share", d.global_share)),
    )
    local = t.get("local", {})
    _check(local, {"memory", "workers", "fail-fast"}, f"{where} local")
    workers = local.get("workers", "auto")
    cfg.local = LocalConfig(
        memory=_percent(local["memory"], where) if "memory" in local else 0.5,
        workers=None if workers == "auto" else int(workers),
        fail_fast=bool(local.get("fail-fast", True)),
    )
    return cfg


def load(root: str) -> Config:
    pyproject = Path(root) / "pyproject.toml"
    tool = tomllib.loads(pyproject.read_text()).get("tool", {}) if pyproject.exists() else {}
    if "fastci" in tool:
        return _parse(tool["fastci"], "pyproject.toml [tool.fastci]", "pyproject")
    own = Path(root) / "fastci.toml"
    if own.exists():
        return _parse(tomllib.loads(own.read_text()), "fastci.toml", "fastci.toml")
    if "testmap" in tool:
        print(
            "fastci: [tool.testmap] is deprecated; rename it to [tool.fastci]"
            " (global_share becomes [tool.fastci.tests] global-share)",
            file=sys.stderr,
        )
        old = tool["testmap"]
        cfg = Config(source="testmap")
        cfg.tests.full = list(old.get("full", []))
        cfg.tests.always = list(old.get("always", []))
        cfg.tests.global_share = float(old.get("global_share", 1.0))
        return cfg
    return Config()
