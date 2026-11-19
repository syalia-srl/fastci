"""Which tier fastci runs in, read from the environment.

The tiers are tables in the configuration (``config.default_tiers``): local on
a laptop, pr in a pull request, main on a push to the default branch, release
on a release branch or a version tag. The first whose ``when`` matches wins.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass, field

from .config import TierSpec


class TierError(ValueError):
    pass


@dataclass(frozen=True)
class Context:
    tier: str
    base: str | None  # a ref to diff against; None when the tier runs every test
    ci: bool
    reason: str  # one line, printed by `fastci plan`
    spec: TierSpec = field(default_factory=TierSpec)


def _matches(when: dict, env: Mapping[str, str], default_branch: str) -> str | None:
    """What matched, as words, or None."""
    said = []
    if "ci" in when:
        if bool(env.get("CI")) != when["ci"]:
            return None
        said.append("CI set" if when["ci"] else "no CI variable")
    if "event" in when:
        event = env.get("GITHUB_EVENT_NAME", "")
        if event not in when["event"]:
            return None
        said.append(f"event {event}")
    if "ref" in when:
        ref = env.get("GITHUB_REF", "")
        globs = [g.replace("{default-branch}", default_branch) for g in when["ref"]]
        if not any(fnmatch.fnmatchcase(ref, g) for g in globs):
            return None
        said.append(ref)
    return ", ".join(said) or "always"


def detect(
    env: Mapping[str, str],
    default_branch: str,
    tiers: Mapping[str, TierSpec],
    override: str | None = None,
) -> Context:
    if override:
        if override not in tiers:
            raise TierError(f"no tier named '{override}'; the tiers are {', '.join(tiers)}")
        name, why = override, "asked for"
    else:
        found = (
            (n, _matches(t.when, env, default_branch))
            for n, t in tiers.items()
            if t.when is not None
        )
        name, why = next(((n, w) for n, w in found if w), (None, None))
        if name is None:
            raise TierError(f"no tier matches this environment; the tiers are {', '.join(tiers)}")
    spec = tiers[name]
    base = None
    if spec.tests == "picked":
        base = f"origin/{env.get('GITHUB_BASE_REF') or default_branch}"
    reason = f"tier {name} ({why})" + (f", diffed against {base}" if base else "")
    return Context(name, base, bool(env.get("CI")), reason, spec)
