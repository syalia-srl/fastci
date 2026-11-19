# fastci

Run, in each case, only the checks and tests a change can break, cheapest
first, and stop at the first failure.

fastci was called testmap up to 0.1.0.

```bash
uvx fastci setup          # propose the config, the workflow and the test marks
uvx fastci setup --write  # apply them
uvx fastci                # run what this case needs
uvx fastci plan           # print what it would run, and why
uvx fastci guide          # how to move a repository's CI to fastci
```

## What runs where

`fastci` reads where it runs from the environment (`--tier` overrides it):

| Tier | Where | What runs |
|---|---|---|
| T0 | a laptop | lock, format, lint and type checks on changed files; tests marked `always`; the tests the change can break. Capped workers and memory, `nice 10`, stops at the first failing test. |
| T1 | a pull request | the same, every failure reported |
| T2 | a push to the default branch | every check and the whole fast suite; a second job records the map |
| T3 | a `release/**` branch or `v*` tag | T2, then the tests marked `slow`, `gpu`, `browser`, `e2e` or `bench` |

The checks come from what the project uses: `uv lock --check`, `ruff format
--check`, `ruff check`, and the first of `ty`, `mypy` or `pyright` it finds.

## The map

On the default branch, `fastci map record` runs the suite once and records, for
every test, the lines of your code it runs (subprocesses included) and the
files of your repository it reads. In a pull request, and on a laptop signed in
with `gh`, fastci downloads the newest map and runs the tests that ran the
changed lines or read the changed files. Without a map, a pull request runs
every test and a laptop runs only the changed test files.

The default branch keeps running the whole suite: what a pull request misses
shows up on the next merge. `fastci map pick --base origin/main --why` says why
each test was picked.

## Rules

For every path the diff touches:

| The diff changes… | fastci map runs… |
|---|---|
| the lockfile, `pyproject.toml`, a root `conftest.py`, a workflow | every test |
| a test file | that file |
| a line inside a function | the tests that ran that line; for a new line, the tests that ran the function |
| a line at module level | the tests that ran any of that file; for an import, the fastest one |
| a blank line, a comment, a docstring | nothing |
| a file some test read (CSS, JSON, a script) | the tests that read it, and the tests whose source names it |

Tests marked `@pytest.mark.always` run in every pull request. Use it for tests
that compare a generated file with its source.

## Configuration

In `pyproject.toml` (or the same keys at the top level of `fastci.toml`). Every
key is optional; these are the defaults.

```toml
[tool.fastci]
default-branch = "main"

[tool.fastci.stages]              # replace a check, add one, or turn one off with ""
# types = "mypy src"
# docs  = { run = "make docs-check", tiers = ["T1", "T2"], after = "lint" }

[tool.fastci.tests]
args = []                         # passed to pytest
full = []                         # paths that mean "run everything"
always = []                       # test files every tier runs
release-only = ["slow", "gpu", "browser", "e2e", "bench"]
slow-seconds = 10                 # what setup --from-map marks slow
global-share = 1.0                # see DESIGN.md

[tool.fastci.local]
memory = "50%"                    # share of available memory for the whole run
workers = "auto"                  # pytest-xdist workers
fail-fast = true
```

`[tool.testmap]` is still read, with a warning, when neither exists.

## In GitHub Actions

`fastci setup` writes `.github/workflows/fastci.yml`: one job that runs
`uvx fastci@X.Y.Z` on pull requests, on pushes to the default branch and
`release/**`, and on `v*` tags; and a job on the default branch that records
the map and uploads it as the artifact `fastci`. The check job needs
`fetch-depth: 0` and `permissions: actions: read`.

## Limits

Python and pytest only in this version; JavaScript and TypeScript, path suites for other languages, and browser and end-to-end suites come next. Code that a test reaches through a non-Python process
(a shell script, a Node server) is linked to the test only when the test opens
the file itself or names it in its source.
