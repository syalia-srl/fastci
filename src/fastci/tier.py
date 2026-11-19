"""Which of the four cases fastci runs in, read from the environment.

T0 a laptop, T1 a pull request, T2 a push to the default branch, T3 a release
branch or a version tag.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Context:
    tier: str  # "T0" | "T1" | "T2" | "T3"
    base: str | None  # a ref to diff against; None in T2 and T3
    ci: bool
    reason: str  # one line, printed by `fastci plan`


def detect(env: Mapping[str, str], default_branch: str, override: str | None = None) -> Context:
    ci = bool(env.get("CI"))
    main = f"origin/{default_branch}"
    if override:
        base = main if override in ("T0", "T1") else None
        return Context(override, base, ci, f"tier {override} asked for")
    if not ci:
        return Context("T0", main, False, f"no CI variable: a laptop, diffed against {main}")
    ref = env.get("GITHUB_REF", "")
    if env.get("GITHUB_EVENT_NAME") == "pull_request":
        base = f"origin/{env.get('GITHUB_BASE_REF') or default_branch}"
        return Context("T1", base, True, f"a pull request into {base}")
    if ref.startswith("refs/tags/v") or ref.startswith("refs/heads/release/"):
        return Context("T3", None, True, f"a release: {ref}")
    if ref == f"refs/heads/{default_branch}":
        return Context("T2", None, True, f"a push to {default_branch}")
    return Context("T1", main, True, f"a push to {ref or 'an unknown ref'}, diffed against {main}")
