# testmap

Run in each pull request only the tests a change can break.

testmap runs your pytest suite once, on main, and records two things for every
test: the lines of your code it runs (subprocesses it starts included) and the
files of your repository it reads. That record is the map. In a pull request,
testmap reads the diff, looks up which tests ran the changed lines or read the
changed files, and runs those.

```bash
# on main, once per merge or once a night
uv run --with testmap testmap record -- -n auto

# in a pull request, or before you push
uv run --with testmap testmap pick --base origin/main --why
uv run --with testmap testmap run --base origin/main -- -n auto
```

Main keeps running the whole suite: what a pull request misses shows up on the
next merge.

## Rules

For every path the diff touches:

| The diff changes… | testmap runs… |
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

In `pyproject.toml`:

```toml
[tool.testmap]
full = ["data/schema.sql"]        # more paths that mean "run everything"
always = ["tests/test_docs.py"]   # files that always run
global_share = 0.1                # optional: see DESIGN.md
```

## In GitHub Actions

```yaml
# on push to main: record and upload
- run: uv run --with "testmap @ git+https://github.com/syalia-srl/testmap@v0.1.0" testmap record --out map/map.json.gz -- -n auto
- uses: actions/upload-artifact@v4
  with: { name: testmap, path: map/map.json.gz }

# on pull_request: fetch the newest map from main and run what it picks
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- run: |
    uv run --with "$TESTMAP" testmap fetch
    uv run --with "$TESTMAP" testmap run --base origin/${{ github.base_ref }} -- -n auto
  env:
    GH_TOKEN: ${{ github.token }}
```

The job needs `permissions: actions: read` to fetch. Without a map, `run` runs
every test.

## Limits

Python and pytest only. Code that a test reaches through a non-Python process
(a shell script, a Node server) is linked to the test only when the test opens
the file itself or names it in its source.
