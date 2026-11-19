# Design

This file changes only when the architecture changes.

## Components

- **The plugin** (`testmap.plugin`) runs inside pytest. When recording, it puts
  each test's id where a child process can read it and notes every repository
  file a test opens, through Python's audit hook on `open`. When running, it
  deselects the tests that were not picked.
- **The subprocess hook** (`_subprocess/sitecustomize.py`) runs in every Python
  child a test starts while recording: it labels that child's coverage with the
  test and notes the files the child opens.
- **coverage.py** records the lines each test runs (dynamic contexts), in the
  test process and, through its subprocess patch, in child processes.
- **record** joins both records into one map: per source file, per function
  and per line, the tests that ran it; per file read, the tests that read it;
  per test, its duration.
- **pick** reads a git diff and the map and applies the rules in `README.md`.
- **fetch** downloads the newest map that main's CI uploaded.

## Rules and their reasons

- **Functions are matched by name and lines by their text.** The map is
  recorded on one commit of main and used against pull requests based on
  later ones. Names and line text survive the drift that line numbers do not.
- **Main runs the whole suite.** No selection is perfect. A test a pull request
  missed fails on the next merge, which is when it gets fixed and, if the miss
  was a rule's fault, when the rule gets a fixture case.
- **Recording never gates.** Recording slows the suite (43% and 100% on the two
  repositories measured), and timing-sensitive tests can fail under it.
- **`global_share` is off by default.** When almost every test boots the same
  server, almost every function is "run" by almost every test, and line-level
  selection still picks most of the suite. `global_share` narrows a unit that
  more than that share of tests ran to the tests that name it plus the five
  fastest. On aegis it cut the median from 90% to 65% of the suite's time, and
  without an `always` group for coherence tests it lost real failures.

## What a machine checks and what a reader judges

The tests check each rule on a fixture project, including a mutation check for
the subprocess hook. Whether a repository's history is safe under testmap is
measured by replaying its past pull requests; that replay is not part of this
package yet.

## Non-goals

- Test runners other than pytest, and languages other than Python.
- Picking tests for code reached only through a non-Python process.
- Replacing the full run on main.
