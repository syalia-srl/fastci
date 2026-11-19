# Changelog

## 0.2.0

testmap is now fastci.

- `fastci` runs, per tier (laptop, pull request, default branch, release), the
  lock, format, lint and type checks and the tests a change can break, cheapest
  first, stopping at the first failure. `fastci plan` prints the plan and why.
- On a laptop: pytest-xdist workers sized from free memory, the run capped with
  a systemd user scope, `nice 10`, fail-fast; GPU tests never run without a GPU.
- `fastci setup` proposes `[tool.fastci]`, the workflow, `.gitignore` and test
  marks; `--diff`, `--write`, `--from-map`, `--check`. `fastci guide`, `fastci fix`.
- Marks `always`, `slow`, `gpu`, `browser`, `e2e`, `bench`, registered by the
  plugin's pytest11 entry point.
- Config in `[tool.fastci]` or `fastci.toml`; `[tool.testmap]` still read.
- The map is version 2 (records peak memory); version 1 maps still load.
- Migration: `testmap record|pick|run|fetch` are `fastci map record|pick|run|fetch`;
  `testmap run` in CI becomes `fastci`. Maps uploaded as `testmap` are still fetched.

## 0.1.0

- `testmap record`, `pick`, `run` and `fetch`.
- Per-test lines (subprocesses included) and per-test files read.
- Line, function, module and read-file rules; `always` marker and list; `full` patterns; optional `global_share`.
