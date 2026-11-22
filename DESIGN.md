# Design

This file changes only when the architecture changes.

## Components

The core knows git, tiers, stages, caps and the map file; everything about a
language lives in an adapter (`fastci.adapter.Adapter`). The full design,
including the adapters, suites and end-to-end maps still to come, is
`docs/superpowers/specs/2026-11-19-fastci-design.html`.

- **The tier** (`fastci.tier`) picks the first configured tier whose `when`
  matches the CI environment; the defaults (`fastci.config.default_tiers`) are
  local, release, main and pr. The tier's table says what the rest does.
- **The plan** (`fastci.plan`) orders the stages for that tier: every project's
  checks, then the tests marked always, then the picked tests, then on a
  the release-only marks the tier lists. Each step carries the reason it runs or is
  skipped.
- **Execution** (`fastci.execute`) runs the steps and stops at the first that
  fails; on a laptop each command runs under the caps from `fastci.local`.
- **Setup** (`fastci.setup`) reads a repository and proposes the config, the
  workflow, the test marks and the trimmed old workflows; it never runs the suite.
- **The Python adapter** (`fastci.python`) finds a project's tools and runs
  pytest with the project's own Python, putting fastci on `PYTHONPATH` so the
  plugin loads without being installed there.

- **The plugin** (`fastci.python.plugin`) runs inside pytest. When recording, it puts
  each test's id where a child process can read it and notes every repository
  file a test opens, through Python's audit hook on `open`. When running, it
  deselects the tests that were not picked. It writes a results file for fastci:
  each test as it starts and its outcome, and at the end the failures pytest
  counted and whether it stopped early, so a test with a start and no outcome
  is the one a crash killed. It reads the flagged tests (the flagged file
  fastci writes and the flag mark) and reports a flagged test's failure as an
  expected one, or deselects it.
- **The subprocess hook** (`_subprocess/sitecustomize.py`) runs in every Python
  child a test starts while recording: it labels that child's coverage with the
  test and notes the files the child opens.
- **coverage.py** records the lines each test runs (dynamic contexts), in the
  test process and, through its subprocess patch, in child processes.
- **record** joins both records into one map: per source file, per function
  and per line, the tests that ran it; per file read, the tests that read it;
  per test, its duration.
- **pick** reads a git diff and the map and applies the rules in the book (`docs/chapters/04-why-a-test-ran.md`).
- **fetch** downloads the newest map that main's CI uploaded.
- **The flaky module** (`fastci.flaky`) says what a change reaches (`Reach`),
  judges each test that failed and then passed (`judge`, one `Verdict` per
  test) and keeps main's history of flagged and forgiven tests (`History`,
  the `fastci-flaky` artifact). `fastci.execute` retries the failed tests and
  calls it; its `Flagger` reads and writes the flags around the test stages.
- **The issues module** (`fastci.issues`) keeps one GitHub issue per flagged
  test, found by the node id in a marker line of its body, never by title.

- **`fastci.report`**: `fastci report`. Reads one or more maps of the default
  branch, the source tree and the flaky history, builds a dict of sections
  (time, overlap, coverage, flaky) and prints it as text, JSON or one HTML page.
  It reads the map's version 3 fields: lines run outside tests, executable
  lines, per-test phases, fixture runs, child processes and the lane.

## Rules and their reasons

- **Functions are matched by name and lines by their text.** The map is
  recorded on one commit of main and used against pull requests based on
  later ones. Names and line text survive the drift that line numbers do not.
- **Main runs the whole suite.** No selection is perfect. A test a pull request
  missed fails on the next merge, which is when it gets fixed and, if the miss
  was a rule's fault, when the rule gets a fixture case.
- **Recording never gates.** Recording slows the suite (43% and 100% on the two
  repositories measured), and timing-sensitive tests can fail under it.
- **A retry never forgives what the change reaches.** A test that fails and
  then passes is green only when the change (on main, the commits since the
  last green run) does not reach it, it did not crash its process, and fewer
  than `machine-threshold` tests (three by default) passed on the retry. A race
  a change brings in shows up as a flake, so forgiving every pass on retry
  would merge it; a broken runner fails the same way every time, so the retry
  runs on the same machine. When fastci cannot tell what a change reaches,
  every test counts as reached.
- **`global_share` is off by default.** When almost every test boots the same
  server, almost every function is "run" by almost every test, and line-level
  selection still picks most of the suite. `global_share` narrows a unit that
  more than that share of tests ran to the tests that name it plus the five
  fastest. On aegis it cut the median from 90% to 65% of the suite's time, and
  without an `always` group for coherence tests it lost real failures.

- **The report gives evidence, never a verdict, and never fails a run.**
  Coverage cannot see assertions: two tests that run the same lines may check
  different things. Each finding carries its numbers and the reader decides.
  Its thresholds (15 own lines for a group, 90% for almost the same, a call of
  at least 0.5 s for much time and little code) come from judging its findings
  on five real repositories, and the report reads only the map, so it can be
  rebuilt in seconds without running a test.

## What a machine checks and what a reader judges

The tests check each rule on a fixture project, including a mutation check for
the subprocess hook. Whether a repository's history is safe under fastci is
measured by replaying its past pull requests; that replay is not part of this
package yet.

## Non-goals

- Building, publishing or deploying: fastci only checks and tests.
- Knowing any repository's own tools: those enter as stages, plain commands.
- Picking tests for code reached only through a non-Python process (until the
  end-to-end map).
- Replacing the full run on the default branch.
- Memory caps outside Linux with a systemd user session.
