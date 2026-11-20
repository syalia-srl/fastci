# Changelog

## Unreleased

- A failed test is retried once, serially, in a fresh pytest process on the same
  machine. If it passes, the verdict depends on what the change reaches: a test
  the change does not reach goes green; one it reaches stays red, as do a crash,
  a second failure, and three or more tests passing on the retry (the machine).
- Main and release branches judge against the commits since the last green run
  of the same workflow on the same branch. Without a map, a token, a green run
  or that run's commit, every test counts as reached and the run says why.
- A forgiven failure never hides anything else: the stage stays red when
  pytest counted a failure no test explains (a collection error, a coverage
  threshold), and runs on when pytest stopped early, even from `-x` in
  `addopts` or `PYTEST_ADDOPTS`. A test the map does not know counts as
  reached. On main, a green run at a commit after the one under test is
  skipped.
- Only SIGABRT, SIGBUS, SIGSEGV, SIGKILL, SIGILL and SIGFPE blame the test
  that was running; SIGTERM and SIGINT do not.
- `[tool.fastci.flaky]`: `retries`, `machine-threshold`, `reach-share`,
  `forgive-reached`; a tier table takes `retries`. A value of the wrong type or
  out of range is a config error naming its key.
- A forgiven test is flagged: as a GitHub issue labelled `flaky`, as a
  `quarantine` mark fastci asks you to add, or both (`store`, `label`, `mark`,
  `flagged`). A flagged test still runs, but its failure no longer blocks. Each
  push to the default branch records the flagged and forgiven tests' outcomes,
  once per run, in the `fastci-flaky` artifact; a history that exists but
  cannot be fetched or read is left as it was. `retries = 0` turns flags off
  on that tier, and a laptop reads the flags within 5 seconds.
- The book: a chapter on flaky tests.

## 0.2.1

- `fastci map record --source` ran pytest with fastci's own interpreter, not
  the project's: without the project's dependencies or pytest-xdist the run
  failed and wrote an empty map. It now goes through the adapter, as without
  `--source`.
- An empty map counts as no map: every test runs, and fastci says why. Before,
  a pull request picked nothing from it.
- `fastci --version`.

## 0.2.0

testmap is now fastci.

- `fastci` runs, per tier (`local`, `pr`, `main`, `release`), the
  lock, format, lint and type checks and the tests a change can break, cheapest
  first, stopping at the first failure. `fastci plan` prints the plan and why.
- On a laptop: pytest-xdist workers sized from free memory, the run capped with
  a systemd user scope, `nice 10`, fail-fast; GPU tests never run without a GPU.
- `fastci setup` proposes `[tool.fastci]`, the workflow, `.gitignore` and test
  marks; `--diff`, `--write`, `--from-map`, `--check`. `fastci guide`, `fastci fix`.
- Marks `always`, `slow`, `gpu`, `browser`, `e2e`, `bench`, registered by the
  plugin's pytest11 entry point.
- Config in `[tool.fastci]` or `fastci.toml`; `[tool.testmap]` still read.
  Each tier is a table under `[tool.fastci.tiers]`: change any key of a default
  tier, or add your own.
- The book: a Quick Start, a User Guide and a Developer Guide, every command in
  it run when it is built, published at https://syalia-srl.github.io/fastci/
  with a PDF.
- fastci is not on PyPI yet: it runs as
  `uvx --from git+https://github.com/syalia-srl/fastci@v0.2.0 fastci`, and the
  workflow `fastci setup` writes does the same. The release workflow can publish
  to PyPI once the repository variable `PYPI_PUBLISH` is `true`.
- A project without uv runs its tests with `python3` when there is no `python`.
- The map is version 2 (records peak memory); version 1 maps still load.
- Migration: `testmap record|pick|run|fetch` are `fastci map record|pick|run|fetch`;
  `testmap run` in CI becomes `fastci`. Maps uploaded as `testmap` are still fetched.

## 0.1.0

- `testmap record`, `pick`, `run` and `fetch`.
- Per-test lines (subprocesses included) and per-test files read.
- Line, function, module and read-file rules; `always` marker and list; `full` patterns; optional `global_share`.
