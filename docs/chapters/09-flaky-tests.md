# Flaky tests

A flaky test fails and then passes on the same code. It might wait on a timer, race another thread, or depend on what an earlier test left behind. If every such failure turns the run red, people learn to press "re-run" without reading. If every one is forgiven, a race that a change brought in gets merged.

fastci retries a failed test once. When the test passes on the retry, fastci decides whether the failure blocks, using the same map that picks the tests.

## The retry

When a test stage fails, fastci runs the tests that failed again, once each, one after the other, in a new pytest process on the same machine. It keeps the stage's pytest arguments and removes pytest-xdist's `-n`, `--numprocesses` and `--dist`, so the retry runs serially. A new process starts without the module state the failed test left behind. Using the same machine means a broken runner still shows up as broken.

There is a cost to this. A test that fails only because an earlier test left something behind will pass in a new process. When fastci forgives such a test, it says the test passed in a fresh process and may depend on test order, so whoever fixes it does not hunt for a race.

## The verdict

Each test that failed gets one verdict. The first row that matches decides:

| What happened | Verdict |
|---|---|
| the test killed its pytest process | red: a fault in the code or the machine, never forgiven |
| it failed again on the retry | red |
| three or more tests failed and then passed in this run | red: this looks like the machine, not the code |
| it passed on the retry, and the change reaches it | red: check for a race in what the change touched |
| it passed on the retry, and the change does not reach it | green |

A change reaches a test when the map says the test ran a line the change touched, or read a file it changed, or when the change edits the test's own file. Code that runs under nearly every test, such as an application's startup, does not count: a test is reached through a line only if at most `reach-share` of the suite ran that line (a fifth by default). Files that run only because they are listed in `always` are not reached either. A change to a path that makes fastci run every test, such as a lockfile, `pyproject.toml`, a `conftest.py` or a CI workflow, reaches every test through that path.

When fastci cannot tell what a change reaches, for example without a map, every test counts as reached and fails the run. fastci says so.

```{python echo=false output=false}
from bookkit import Demo

demo = Demo()
marker = demo.root.parent / "marker"
demo.write(
    "tests/test_once.py",
    f"""\
    import os

    import pytest


    @pytest.mark.always
    def test_once():
        if not os.path.exists({str(marker)!r}):
            open({str(marker)!r}, "w").close()
            raise AssertionError("fails until the marker exists")
    """,
)
marker.touch()
demo.sh("fastci map record")
demo.commit("a test that fails once", merge=True)
```

Here the shop has a test, `test_once`, marked `always`, that fails the first time it runs and passes after that. A pull request changes `discount`, which `test_once` never runs:

```{python continue}
demo.edit("src/shop/cart.py", "price * (1 - pct / 100)", "price - price * pct / 100")
demo.commit("discount without the parentheses")
marker.unlink()
demo.sh("fastci", env={"CI": "true"})
```

The test failed, passed on the retry, and the change does not reach it, so the run is green. If the pull request edits the test itself, the same failure keeps the run red:

```{python continue}
demo.edit("tests/test_once.py", "fails until", "fails once until")
demo.commit("reword the failure")
marker.unlink()
demo.sh("fastci", env={"CI": "true"})
```

On a pull request, most failing tests were picked because the change reaches them, so a pass on the retry still leaves the run red. Pull requests forgive mainly tests marked `always`.

## On main and on a release branch

The default branch and a release branch run every test, so their diff is a different one: the commits since the last green run of the same workflow on the same branch. A race that a pull request brought in and that did not show in its own run must not be forgiven when it shows right after the merge.

The verdicts name that diff instead of a change:

```text
fastci: tests/test_links.py::test_reads_across_a_link
          failed, then passed; no commit since the last green run reaches it
          passed in a fresh process: it may depend on test order
fastci: tests/test_api.py::test_upload_resumes
          failed, then passed; the commits since 9f8e7d6 reach it through src/api/upload.py:resume
          red: check for a race in what those commits touched
```

To find that run, fastci asks the GitHub API for the newest successful push run of the workflow on the branch. It needs the map from the default branch, the variables GitHub Actions sets (`GITHUB_REPOSITORY`, `GITHUB_WORKFLOW_REF`, `GITHUB_RUN_ID`) and a token with `actions: read`. It also needs that run's commit in the clone, so check out with `fetch-depth: 0`. When something is missing (no map, no token, no green run yet, or a commit a shallow clone does not have), every test counts as reached, and the run says why in one line:

```text
fastci: git does not have 3f2a9c1e07, the last green run on main; every test counts as reached
```

## Configuration

The settings are in `[tool.fastci.flaky]`. These are the defaults:

```toml
[tool.fastci.flaky]
retries = 1             # reruns of a failed test; 0 turns the retry and the verdict off
machine-threshold = 3   # this many tests passing on the retry blame the machine
reach-share = 0.2       # a line run by more of the suite than this reaches no test
forgive-reached = false # true: a test the change reaches is forgiven too
```

A tier can change the number of retries:

```toml
[tool.fastci.tiers.local]
retries = 0
```

## How it is built

`FlakyConfig` holds the settings above. The pytest plugin writes a results file during the run, which `Results` reads: the tests that failed, and the one that was running when the process died. `Reach` is what a change reaches, built from the pick, and `judge` turns the failures, the retry and the reach into one `Verdict` per test, following the table above. `FlakyRun` carries the settings, the number of retries and the reach to the test stages.
