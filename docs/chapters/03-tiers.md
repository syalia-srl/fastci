# Tiers: what runs where

A change goes through several places before it ships, and each can afford a different amount of testing. On a laptop you want an answer in seconds. A pull request can take a few minutes, the default branch about ten, and a release as long as it takes. fastci calls each of these a tier. A tier says how fastci recognises the place, whether it runs the tests the change can break or the whole suite, and which of the slow classes of tests it adds.

fastci ships four tiers, tried in this order:

| Tier | When | What runs |
|---|---|---|
| `local` | no `CI` variable: a laptop | the checks on changed files, the tests marked `always`, the tests the change can break; capped, stops at the first failing test |
| `release` | a `release/*` branch or a `v*` tag | every check, the whole fast suite, then the tests marked `slow`, `gpu`, `browser`, `e2e` or `bench` |
| `main` | a push to the default branch | every check and the whole fast suite |
| `pr` | any other CI run | the same as `local`, every failure reported, uncapped |

```{python echo=false output=false}
from bookkit import Demo

demo = Demo()
demo.write(
    "tests/test_receipt.py",
    """\
    import pytest

    from shop.cart import total


    @pytest.mark.slow
    def test_a_long_receipt():
        assert total(["apple"] * 1000) == 2000
    """,
)
demo.commit("a slow test", merge=True)
```

Our shop now has a slow test, marked `slow`. `--tier` runs any tier's plan wherever you are, so you can see what the default branch would run:

```{python continue}
demo.sh("fastci plan --tier main")
```

and what a release would add:

```{python continue}
demo.sh("fastci plan --tier release")
```

The `marked` stage is the release's own: it runs the tests whose mark the tier lists, and leaves out `gpu` on a machine without a GPU.

## Changing a tier

Each tier is a table under `[tool.fastci.tiers]` in `pyproject.toml` (or at the top level of a `fastci.toml`). A table you write changes only the keys it names, so this is enough to run the slow tests on every merge as well:

```{python continue}
demo.edit(
    "pyproject.toml",
    "[tool.pytest.ini_options]",
    """[tool.fastci.tiers.main]
marks = ["slow"]

[tool.pytest.ini_options]""",
)
demo.sh("fastci plan --tier main")
```

The keys of a tier:

- `when`: how fastci recognises it. `ci = true` or `false` tests the `CI` variable, `event` lists GitHub event names, and `ref` lists globs over `GITHUB_REF`, where `{default-branch}` stands for the default branch. Every key given must match. A tier without `when` runs only when asked for with `--tier`.
- `tests`: `"picked"`, the tests the change can break against the merge-base with the default branch, or `"all"`, the whole fast suite.
- `without-map`: what `"picked"` does when there is no map yet. `"all"` runs every test, and `"changed"` runs only the changed test files (the `local` default, because a laptop should stay fast).
- `marks`: the release-only marks this tier runs, after the fast suite. Which marks count as release-only is `[tool.fastci.tests] release-only`.
- `fail-fast`: stop at the first failing test. It is on for `local`.
- `capped`: apply the laptop's limits (the [laptop chapter](on-a-laptop.html)). It is on for `local`.

## A tier of your own

Tiers you add are tried before the defaults, under any name you like. A nightly run on a schedule is a common one:

```{python continue}
demo.edit(
    "pyproject.toml",
    "[tool.pytest.ini_options]",
    """[tool.fastci.tiers.nightly]
when = { event = "schedule" }
tests = "all"
marks = ["slow", "e2e", "bench"]

[tool.pytest.ini_options]""",
)
demo.sh(
    "fastci plan",
    env={"CI": "true", "GITHUB_EVENT_NAME": "schedule", "GITHUB_REF": "refs/heads/main"},
)
```

A scheduled run on `main` would match the `main` tier too, but `nightly` is tried first. A name `--tier` does not know is an error that lists the tiers there are:

```{python continue}
demo.sh("fastci plan --tier T1")
```
