"""fastci's configuration: ``[tool.fastci]`` in pyproject.toml, else fastci.toml.

``[tool.testmap]`` is still read when neither exists, so repositories that
adopted fastci under its old name keep working.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RELEASE_ONLY = ["slow", "gpu", "browser", "e2e", "bench"]


class ConfigError(ValueError):
    pass


@dataclass
class TierSpec:
    # when: all keys must match; ci (bool), event (names), ref (globs, {default-branch}
    # stands for the default branch). None: only reached with --tier.
    when: dict | None = None
    tests: str = "picked"  # "picked": what the change can break; "all": the whole fast suite
    without_map: str = "all"  # picked with no map: "all" or "changed" (changed test files)
    marks: list[str] = field(default_factory=list)  # release-only marks that run here
    fail_fast: bool = False
    capped: bool = False  # [tool.fastci.local] caps, nice 10, gh's token, short timeouts
    retries: int | None = None  # None: [tool.fastci.flaky] retries


def default_tiers() -> dict[str, TierSpec]:
    """Tried in this order, after the tiers a repository adds."""
    return {
        "local": TierSpec(when={"ci": False}, without_map="changed", fail_fast=True, capped=True),
        "release": TierSpec(
            when={"ref": ["refs/heads/release/*", "refs/tags/v*"]},
            tests="all",
            marks=list(RELEASE_ONLY),
        ),
        "main": TierSpec(when={"ref": ["refs/heads/{default-branch}"]}, tests="all"),
        "pr": TierSpec(when={"ci": True}),
    }


@dataclass
class StageSpec:
    run: str  # a shell command; "" turns the stage off
    tiers: frozenset[str] | None = None  # None: every tier
    after: str | None = None  # name of the stage it runs after


@dataclass
class TestsConfig:
    __test__ = False

    args: list[str] = field(default_factory=list)
    full: list[str] = field(default_factory=list)  # added to the picker's DEFAULT_FULL
    always: list[str] = field(default_factory=list)
    release_only: list[str] = field(default_factory=lambda: list(RELEASE_ONLY))
    slow_seconds: float = 10.0
    global_share: float = 1.0


@dataclass
class LocalConfig:
    memory: float = 0.5  # share of MemAvailable
    workers: int | None = None  # None = auto


@dataclass
class FlakyConfig:
    retries: int = 1  # reruns of a failed test, serially, in one fresh pytest
    machine_threshold: int = 3
    store: str = "issues"  # where flags live: "issues", "marks" or "both"
    label: str = "flaky"  # the issues' label
    mark: str = "quarantine"  # the pytest mark
    flagged: str = "run"  # a flagged test: "run" (failure reported as xfail) or "skip"
    unflag_after: int = 20
    reach_share: float = 0.2
    forgive_reached: bool = False


@dataclass
class Config:
    default_branch: str = "main"
    stages: dict[str, StageSpec] = field(default_factory=dict)
    tests: TestsConfig = field(default_factory=TestsConfig)
    local: LocalConfig = field(default_factory=LocalConfig)
    tiers: dict[str, TierSpec] = field(default_factory=default_tiers)
    flaky: FlakyConfig = field(default_factory=FlakyConfig)
    source: str = "defaults"  # "pyproject", "fastci.toml", "testmap" or "defaults"

    def retries_for(self, tier: str) -> int:
        retries = self.tiers[tier].retries
        return self.flaky.retries if retries is None else retries


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
    tiers = frozenset(value["tiers"]) if "tiers" in value else None
    return StageSpec(run=value.get("run", ""), tiers=tiers, after=value.get("after"))


def _names(value, where: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    raise ConfigError(f"{where} must be a string or a list of strings")


def _tiers(table: dict, where: str) -> dict[str, TierSpec]:
    defaults = default_tiers()
    added: dict[str, TierSpec] = {}
    for name, value in table.items():
        w = f"{where} tiers.{name}"
        if not isinstance(value, dict):
            raise ConfigError(f"{w} must be a table")
        _check(
            value, {"when", "tests", "without-map", "marks", "fail-fast", "capped", "retries"}, w
        )
        base = defaults.get(name, TierSpec())
        when = value.get("when", base.when)
        if when is not None:
            _check(when, {"ci", "event", "ref"}, f"{w} when")
            when = {k: v if k == "ci" else _names(v, f"{w} when.{k}") for k, v in when.items()}
        spec = TierSpec(
            when=when,
            tests=value.get("tests", base.tests),
            without_map=value.get("without-map", base.without_map),
            marks=_names(value.get("marks", base.marks), f"{w} marks"),
            fail_fast=bool(value.get("fail-fast", base.fail_fast)),
            capped=bool(value.get("capped", base.capped)),
            retries=_int(value["retries"], w, "retries", 0) if "retries" in value else base.retries,
        )
        if spec.tests not in ("picked", "all"):
            raise ConfigError(f'{w} tests must be "picked" or "all"')
        if spec.without_map not in ("changed", "all"):
            raise ConfigError(f'{w} without-map must be "changed" or "all"')
        if name in defaults:
            defaults[name] = spec
        else:
            added[name] = spec
    return added | defaults


def _percent(value, where: str, key: str = "memory") -> float:
    if isinstance(value, str) and value.endswith("%"):
        try:
            return float(value[:-1]) / 100
        except ValueError:
            pass
    raise ConfigError(f'{key} in {where} must be a percentage such as "50%"')


def _int(value, where: str, key: str, least: int) -> int:
    # TOML's true and false are ints to Python.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{where} {key} must be a whole number, {least} or more")
    if value < least:
        raise ConfigError(f"{where} {key} must be {least} or more")
    return value


def _share(value, where: str, key: str) -> float:
    if isinstance(value, str):
        share = _percent(value, where, key)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        share = float(value)
    else:
        raise ConfigError(f'{where} {key} must be a share such as 0.2 or "20%"')
    if not 0 <= share <= 1:
        raise ConfigError(f'{where} {key} must be between 0 and 1 (or "0%" and "100%")')
    return share


def _of(table: dict, key: str, default, kind: type, where: str, what: str):
    value = table.get(key, default)
    if not isinstance(value, kind):
        raise ConfigError(f"{where} {key} must be {what}")
    return value


def _flaky(table: dict, where: str) -> FlakyConfig:
    w = f"{where} flaky"
    _check(
        table,
        {
            "retries",
            "machine-threshold",
            "store",
            "label",
            "mark",
            "flagged",
            "unflag-after",
            "reach-share",
            "forgive-reached",
        },
        w,
    )
    d = FlakyConfig()
    flaky = FlakyConfig(
        retries=_int(table.get("retries", d.retries), w, "retries", 0),
        machine_threshold=_int(
            table.get("machine-threshold", d.machine_threshold), w, "machine-threshold", 1
        ),
        store=_of(table, "store", d.store, str, w, "a string"),
        label=_of(table, "label", d.label, str, w, "a string"),
        mark=_of(table, "mark", d.mark, str, w, "a string"),
        flagged=_of(table, "flagged", d.flagged, str, w, "a string"),
        unflag_after=_int(table.get("unflag-after", d.unflag_after), w, "unflag-after", 0),
        reach_share=_share(table.get("reach-share", d.reach_share), w, "reach-share"),
        forgive_reached=_of(table, "forgive-reached", d.forgive_reached, bool, w, "true or false"),
    )
    if flaky.store not in ("issues", "marks", "both"):
        raise ConfigError(f'{w} store must be "issues", "marks" or "both"')
    if flaky.flagged not in ("run", "skip"):
        raise ConfigError(f'{w} flagged must be "run" or "skip"')
    if flaky.mark in ("always", *RELEASE_ONLY):
        raise ConfigError(f"{w} mark must not be one of fastci's own marks ({flaky.mark!r})")
    return flaky


def _parse(t: dict, where: str, source: str) -> Config:
    _check(t, {"default-branch", "stages", "tests", "local", "tiers", "flaky"}, where)
    cfg = Config(source=source, default_branch=t.get("default-branch", "main"))
    cfg.tiers = _tiers(t.get("tiers", {}), where)
    cfg.stages = {k: _stage(k, v, where) for k, v in t.get("stages", {}).items()}
    for name, spec in cfg.stages.items():
        unknown = sorted((spec.tiers or set()) - set(cfg.tiers))
        if unknown:
            raise ConfigError(
                f"stage '{name}' in {where} names unknown tiers {unknown};"
                f" the tiers are {', '.join(cfg.tiers)}"
            )
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
    if "marks" not in t.get("tiers", {}).get("release", {}) and "release" in cfg.tiers:
        cfg.tiers["release"].marks = list(cfg.tests.release_only)
    local = t.get("local", {})
    _check(local, {"memory", "workers"}, f"{where} local")
    workers = local.get("workers", "auto")
    cfg.local = LocalConfig(
        memory=_percent(local["memory"], where) if "memory" in local else 0.5,
        workers=None if workers == "auto" else int(workers),
    )
    cfg.flaky = _flaky(t.get("flaky", {}), where)
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
