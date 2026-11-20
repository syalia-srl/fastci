# Moving a repository to fastci

A repository that already has CI has a workflow that runs its tests, often next to lint and type checks. Moving it to fastci means replacing those steps with one `fastci` step, adding the job that records the map on the default branch, and marking the tests that should not run on every change. `fastci setup` reads the repository and proposes all of it. It changes nothing until you ask.

```{python echo=false output=false}
from bookkit import Demo

demo = Demo()
```

```{python continue}
demo.sh("fastci setup")
```

The first line is what fastci found: a Python project managed with uv and tested with pytest. Every check and every stage in the plan comes from that line. A project with ruff in its dev dependencies gets format and lint checks, one with ty, mypy or pyright gets a type check, and one with a `uv.lock` gets the lock check.

`--diff` shows each change in full before you apply it:

```{python continue}
demo.sh("fastci setup --diff")
```

The workflow has two jobs. `check` runs `fastci` on pull requests, on pushes to the default branch and to `release/**`, and on version tags. It needs the whole history (`fetch-depth: 0`) to find the merge-base, and `actions: read` to download the map. `map` runs only on the default branch: it records the map and uploads it as the artifact `fastci`. It is allowed to fail and never gates a merge, because recording slows the suite and timing-sensitive tests can fail under it.

`--write` applies the changes. Commit them like any other change:

```{python continue}
demo.sh("fastci setup --write")
demo.commit("ci: run fastci", merge=True)
```

## What the old CI did that fastci does not

A real repository's old workflow also builds docs, checks a changelog or runs a script of its own. `fastci setup --check` lists every step of the old workflows that fastci does not cover, and exits non-zero while any is left. For each one you either make it a stage (the [stages chapter](stages-checks-of-your-own.html)), keep it in its own workflow when it is not a test (a deploy, a release), or drop it with a reason. Our shop had no CI before, so nothing is left:

```{python continue}
demo.sh("fastci setup --check")
```

## Marks

`fastci setup` also proposes test marks. A test that imports playwright or selenium gets `browser`, and with `--from-map` a test slower than `slow-seconds` gets `slow`. Marked tests leave the fast suite and run only in the tiers that list their mark, by default only on a release (the [next chapter](tiers-what-runs-where.html)). Read the diff of the marks before you apply it: a test that imports playwright only for a type is not a browser test.

## Handing it to an agent

`fastci guide` prints the whole procedure as a checklist, written for whoever does the move, a person or an agent. Give it to an agent together with the repository, and ask it to open a pull request with the plans of a few recent changes (`fastci plan --tier pr --base <commit>`) as evidence:

```{python continue}
demo.sh("fastci guide")
```

The first push to the default branch after the merge uploads the first map. Until then a pull request runs every test and says so.
