# fastci guide

fastci runs, in each case, only the checks and tests a change can break,
cheapest first, and stops at the first failure. This guide is for whoever
moves a repository's CI to it, person or agent.

## What runs where

| Tier | Where | What runs |
|---|---|---|
| T0 | a laptop (no `CI` variable) | lock, format, lint and type checks on changed files; tests marked `always`; the tests the change can break. Capped workers and memory, `nice 10`, stops at the first failing test. Never `slow`, `gpu`, `browser`, `e2e` or `bench`. |
| T1 | a pull request | the same checks and picked tests, every failure reported |
| T2 | a push to the default branch | every check, the whole fast suite; a second job records the map that T0 and T1 pick from |
| T3 | a `release/**` branch or a `v*` tag | T2, then the release-only classes: `slow`, `gpu`, `browser`, `e2e`, `bench` |

`fastci plan` prints what would run here and why, without running it.
`fastci --tier T1 --base origin/main` runs another tier's plan.

## Migrating a repository

1. Read the repository's own agent docs and its current workflows first.
2. Run `fastci setup --diff`. It proposes:
   - a `[tool.fastci]` block, only for values that differ from the defaults;
   - `.github/workflows/fastci.yml`, pinned to the installed version;
   - `.fastci/` in `.gitignore`;
   - marks on tests that look like a class (`browser` for playwright or
     selenium, `gpu` for torch with cuda or cupy);
   - the old workflows without the steps fastci now runs.
3. Apply with `fastci setup --write`. Read the diff of the marks: a test that
   imports playwright only for a type is not a browser test.
4. Run `fastci setup --check`. Each line is a step of the old CI that fastci
   does not run. For each, do one of:
   - make it a stage: `[tool.fastci.stages] docs = "make docs"` (add
     `tiers = ["T1", "T2"]` or `after = "lint"` when it matters);
   - keep it in its own workflow when it is not a test (a deploy, a release);
   - drop it, and say why in the pull request.
   Repeat until `fastci setup --check` exits 0.
5. Run `fastci plan --tier T1 --base HEAD~5` and two or three other recent
   bases. Each shows what that change would have run.
6. Run `fastci` locally. Open the pull request with the plans from step 5,
   a green run, and the CI minutes of the last few pull requests before it.

The first push to the default branch after the merge uploads the first map.
Until then a pull request runs every test and says so.

## Marking tests

- `@pytest.mark.always`: a test that compares a generated file with its
  source (a stub, an index, a copied asset). It fails in every change that
  forgot to regenerate the file, so every tier runs it.
- `slow`, `gpu`, `browser`, `e2e`, `bench`: run only on a release branch.
  `fastci setup --from-map .fastci/map.json.gz` proposes `slow` for tests over
  `slow-seconds` (10 by default) in a map recorded on the default branch.
- `[tool.fastci.tests] release-only` changes which marks wait for a release.

## Reading why a test was picked

`fastci map pick --base origin/main --why` prints each picked test and the
change that picked it: a changed line it ran, a file it read, a test file that
changed, or a path in `full` that runs everything.

## When the default branch catches what a pull request missed

The full run on the default branch exists for this. Fix the test, then fix
the cause of the miss:

- a file every test depends on without reading it (a schema, a fixture
  directory): add it to `[tool.fastci.tests] full`;
- a coherence test: mark it `always`;
- a rule that should have picked the test: open an issue on fastci with the
  change and the test, so the rule gets a fixture case.

## On a laptop

- `[tool.fastci.local] memory = "50%"` caps the whole run at that share of
  available memory, through a systemd user scope where one exists.
- `workers = "auto"` sizes pytest-xdist workers from free memory and the peak
  memory the map recorded; set a number to fix it.
- `fail-fast = false` reports every failing test instead of the first.
- `fastci fix` runs the formatters and fixers, then the checks.
