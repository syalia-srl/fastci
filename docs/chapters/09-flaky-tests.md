# Flaky tests

A flaky test fails and then passes on the same code. It might wait on a timer, race another thread, or depend on what an earlier test left behind. If every such failure turns the run red, people learn to press "re-run" without reading. If every one is forgiven, a race that a change brought in gets merged.

fastci retries a failed test once. When the test passes on the retry, fastci decides whether the failure blocks, using the same map that picks the tests.

## The retry

When a test stage fails, fastci runs the tests that failed again, once each, one after the other, in a new pytest process on the same machine. It keeps the stage's pytest arguments and removes pytest-xdist's `-n`, `--numprocesses` and `--dist`, so the retry runs serially. A new process starts without the module state the failed test left behind. Using the same machine means a broken runner still shows up as broken.

The retry also ignores `-x` and `--maxfail`, wherever they were set, so every test that failed gets its retry. When a stage stopped early at a failure that is then forgiven, fastci runs the stage again without the tests already passed or judged, so a forgiven test never hides the tests after it. fastci learns that the run stopped early from pytest itself, so `-x` in `addopts` or `PYTEST_ADDOPTS` counts too.

fastci forgives only failures that are failed tests. When pytest counted a failure that no test explains, such as a collection error under `--continue-on-collection-errors` or a coverage threshold from pytest-cov's `--cov-fail-under`, the stage keeps its exit code, even if every failed test passed on the retry.

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

When fastci cannot tell what a change reaches, for example without a map, every test counts as reached and fails the run. fastci says so. The same holds for a single test the map does not know, such as a test added after the map was recorded, or one marked `gpu` or `browser` that did not run where the map was recorded:

```text
fastci: tests/test_late.py::test_late
          failed, then passed; the map does not know this test, so it counts as reached
          red: check for a race in what the change touched
```

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

To find that run, fastci asks the GitHub API for the successful push runs of the workflow on the branch, and takes the newest whose commit comes before the one under test. A run re-run green after later pushes, or one that finished after a newer run, can be green at a commit after it; diffing against such a commit would hide the commit's own change, so fastci skips it. It needs the map from the default branch, the variables GitHub Actions sets (`GITHUB_REPOSITORY`, `GITHUB_WORKFLOW_REF`, `GITHUB_RUN_ID`) and a token with `actions: read`. It also needs that run's commit in the clone, so check out with `fetch-depth: 0`. When something is missing (no map, no token, no green run yet, or no green run at a commit before this one that the clone has), every test counts as reached, and the run says why:

```text
fastci: no green run of fastci.yml on main before this commit in this clone; every test counts as reached
fastci: tests/test_api.py::test_upload_resumes
          failed, then passed; fastci cannot tell what the commits since the last green run reach, so it counts as reached
          red: check for a race in what those commits touched
```

## The flag

A forgiven test is flagged, so someone fixes it. A flagged test still runs and its result is counted, but its failure no longer blocks a run and is not retried: pytest reports it as an expected failure, `flagged flaky: #N`. With `flagged = "skip"`, a pull request and a laptop leave flagged tests out; main and release branches still run them.

`store` says where the flags live:

- `"issues"`, the default: one GitHub issue per test, labelled `flaky`. Its body carries the node id in a line `<!-- fastci:test=NODEID -->`, which is how fastci finds it again; the title is only for people. A flake on a pull request, a release branch or a tag opens the issue, or reopens a closed one, and adds a comment naming the pull request (or the branch or tag), the commit and a link to the run. On a push to the default branch, fastci rewrites the body with the test's failure rate and its first and last failure.
- `"marks"`: a test carrying `@pytest.mark.quarantine("#N")` (the mark is the `mark` setting) is flagged. fastci never edits your code; when a test flakes it prints the line to add:

  ```text
  fastci: add @pytest.mark.quarantine("#?") to tests/test_links.py::test_reads_across_a_link
  ```

- `"both"`: either flags a test, and the suggested mark names the issue's number.

Marks are read in every mode, so a repository that already quarantines tests by hand keeps working.

Main measures. Each push to the default branch adds every flagged test's outcome and every forgiven test to `.fastci/flaky.json` (the last 50 outcomes per test) and uploads it as the `fastci-flaky` artifact for 90 days; the next run on main starts from the newest one. A test that two stages run, such as an `always` test in the whole suite, counts once per run, as a failure if either stage failed it. When a history exists but cannot be fetched or read (a server error, a timeout, a file that does not parse), the run says so in one line and writes none, so the upload leaves the newest history as it was; only a repository without one starts empty.

A laptop reads the flags and never writes them. It fetches the open flaky issues with the token it uses for the map, gives up after 5 seconds in all, and keeps a copy in `.fastci/flagged.json` for when it is offline. A test forgiven there prints `failed and then passed; CI will flag it if it does the same there`.

Writing issues needs `issues: write`, which the workflow `fastci setup` writes asks for. A token without it, such as a pull request's from a fork, still gets its verdict: GitHub's refusal costs one line, and the run goes on.

```text
fastci: could not write the flag for tests/test_links.py::test_reads_across_a_link: HTTP Error 403: Forbidden
```

## Configuration

The settings are in `[tool.fastci.flaky]`. These are the defaults:

```toml
[tool.fastci.flaky]
retries = 1             # reruns of a failed test, 0 or more; 0 turns the retry, the verdict and the flags off
machine-threshold = 3   # this many tests passing on the retry blame the machine; 1 or more
reach-share = 0.2       # a line run by more of the suite than this reaches no test; 0 to 1, or "20%"
forgive-reached = false # true: a test the change reaches is forgiven too
store = "issues"        # where flags live: "issues", "marks" or "both"
label = "flaky"         # the issues' label
mark = "quarantine"     # the mark read as a flag, quarantine("#N"); not fastci's or pytest's own
flagged = "run"         # "skip": pull requests and laptops leave flagged tests out
```

A tier can change the number of retries:

```toml
[tool.fastci.tiers.local]
retries = 0
```

## How it is built

`FlakyConfig` holds the settings above. The pytest plugin writes a results file during the run, which `Results` reads: the tests that failed, the one that was running when the process died, and, when the session ends, the failures pytest counted and whether it stopped early. `Reach` is what a change reaches, built from the pick, and `judge` turns the failures, the retry and the reach into one `Verdict` per test, following the table above. `FlakyRun` carries the settings, the number of retries and the reach to the test stages, with a `Flagger` that reads the flags before the first test stage and writes them after each verdict, through the `issues` module, which finds each test's `Issue` by its marker, and the `History` of main's outcomes, one `Entry` per test.
