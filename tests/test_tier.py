import pytest

from fastci import tier as tier_mod


@pytest.mark.parametrize(
    "env,override,tier,base",
    [
        ({}, None, "T0", "origin/main"),
        (
            {"CI": "true", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_BASE_REF": "release/0.3"},
            None,
            "T1",
            "origin/release/0.3",
        ),
        (
            {"CI": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"},
            None,
            "T2",
            None,
        ),
        (
            {"CI": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/release/0.3"},
            None,
            "T3",
            None,
        ),
        (
            {"CI": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v0.3.0"},
            None,
            "T3",
            None,
        ),
        (
            {"CI": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/feature"},
            None,
            "T1",
            "origin/main",
        ),
        ({}, "T3", "T3", None),
    ],
)
def test_detect(env, override, tier, base):
    c = tier_mod.detect(env, "main", override)
    assert (c.tier, c.base) == (tier, base) and c.reason
