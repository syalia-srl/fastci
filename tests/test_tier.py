import pytest

from fastci.config import TierSpec, default_tiers
from fastci.tier import TierError, detect

PUSH = {"CI": "true", "GITHUB_EVENT_NAME": "push"}


@pytest.mark.parametrize(
    "env,override,tier,base",
    [
        ({}, None, "local", "origin/main"),
        (
            {"CI": "true", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_BASE_REF": "release/0.3"},
            None,
            "pr",
            "origin/release/0.3",
        ),
        ({**PUSH, "GITHUB_REF": "refs/heads/main"}, None, "main", None),
        ({**PUSH, "GITHUB_REF": "refs/heads/release/0.3"}, None, "release", None),
        ({**PUSH, "GITHUB_REF": "refs/tags/v0.3.0"}, None, "release", None),
        ({**PUSH, "GITHUB_REF": "refs/heads/feature"}, None, "pr", "origin/main"),
        ({}, "release", "release", None),
    ],
)
def test_default_tiers(env, override, tier, base):
    c = detect(env, "main", default_tiers(), override)
    assert (c.tier, c.base) == (tier, base) and c.reason.startswith(f"tier {tier} (")


def test_default_branch_fills_the_ref():
    c = detect({**PUSH, "GITHUB_REF": "refs/heads/trunk"}, "trunk", default_tiers())
    assert c.tier == "main"


def test_added_tier_is_tried_before_the_defaults():
    tiers = {"nightly": TierSpec(when={"event": ["schedule"]}, tests="all")} | default_tiers()
    env = {"CI": "true", "GITHUB_EVENT_NAME": "schedule", "GITHUB_REF": "refs/heads/main"}
    c = detect(env, "main", tiers)
    assert (c.tier, c.base) == ("nightly", None) and "event schedule" in c.reason


def test_tier_without_when_is_reached_only_by_name():
    tiers = {"deep": TierSpec(tests="all")} | default_tiers()
    assert detect({}, "main", tiers).tier == "local"
    assert detect({}, "main", tiers, "deep").tier == "deep"


def test_unknown_name_and_no_match_are_errors():
    with pytest.raises(TierError, match="no tier named 'T1'; the tiers are local"):
        detect({}, "main", default_tiers(), "T1")
    with pytest.raises(TierError, match="no tier matches"):
        detect({}, "main", {"ci-only": TierSpec(when={"ci": True})})
