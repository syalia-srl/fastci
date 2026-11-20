# How fastci is built

fastci is a small core that knows git, tiers, stages, the laptop's limits and the map file, and an adapter per language that knows how to find a project, list its checks and run its tests. This chapter follows one run of `fastci` through the modules, calling each one by hand on the shop project from the Quick Start.

```{python echo=false output=false}
import os

from bookkit import CLEAN, Demo

demo = Demo()
demo._run("git", "checkout", "-q", "-b", "cheaper-discounts")
demo.edit("src/shop/cart.py", "(1 - pct / 100)", "(100 - pct) / 100")
os.chdir(demo.root)
os.environ.clear()
os.environ.update(CLEAN)
```

## Configuration

`fastci.config.load` reads `[tool.fastci]` from `pyproject.toml`, else `fastci.toml`, else the old `[tool.testmap]`, and returns a `Config`. Its parts are dataclasses: `TestsConfig` (the pytest arguments, `full`, `always`, the release-only marks), `LocalConfig` (the laptop's limits), a `StageSpec` per configured stage, and a `TierSpec` per tier, in the order they are tried. A key fastci does not know raises `ConfigError` with the key's name.

```{python continue}
from fastci.config import load

cfg = load(".")
print(cfg.default_branch, list(cfg.tiers))
print(cfg.tiers["local"])
```

## The tier

`fastci.tier.detect` takes the environment and the tiers and returns a `Context`: the tier's name, the ref to diff against (none when the tier runs every test), whether this is CI, a one-line reason, and the tier's `TierSpec`. A name that `--tier` does not know, or an environment no tier matches, raises `TierError`.

```{python continue}
from fastci.tier import detect

ctx = detect(os.environ, cfg.default_branch, cfg.tiers)
print(ctx.reason)
```

## Projects and the picks

`fastci.plan.gather` asks every adapter which projects the repository holds, lists the changed files since the merge-base, loads the map when there is one, and asks the picker for each project's picks. An adapter answers with a `Project`: its adapter's name, its root, the tools it found, and its `Check`s, each a command, the command that fixes what it finds, and the globs of files it looks at.

```{python continue}
from fastci.plan import gather

demo.sh("fastci map record")
projects, changed, picks, maps = gather(".", ctx, cfg, ".fastci/map.json.gz")
print(projects[0].tools, sorted(changed))
```

The map is a `TestMap` (`fastci.python.mapfile`): the tests, their durations, the tests marked `always`, a `FileEntry` per source file with the tests that ran each function and line, and the files each test read. `Picker` (`fastci.python.pick`) applies the rules of the [why chapter](why-a-test-ran.html) to a diff and returns a `Pick`, with the tests, the test files and a reason for each:

```{python continue}
pick = picks["."]
print(pick.tests, pick.why)
```

## The plan

`fastci.plan.build` turns all of that into a list of `Step`s, each a stage, the project it belongs to, whether it runs or is skipped, the command and the reason. `render` prints them, and that is what `fastci plan` shows:

```{python continue}
from fastci.plan import build, render

steps = build(ctx, cfg, ".", projects, changed, picks, maps, gpu=False)
print(render(ctx, steps))
```

## Running it

`fastci.execute.execute` runs the steps in order and stops at the first red one, naming the stages that did not run. Checks run as shell commands. Test stages go to the project's adapter, which runs pytest on the selection. Before that, `ensure_map` downloads the newest map from the default branch's CI when it is newer than the local one (`fastci.fetch` talks to the GitHub API). On a laptop, `fastci.local` measures free memory, sizes the workers and wraps the command in the systemd scope and `nice`.

## Setup

`fastci.setup` reads the repository for `fastci setup`. `propose` returns a list of `Change`s: a block to add to `pyproject.toml`, a workflow to write, a line to append to `.gitignore`, a mark to put on a test. `diff` shows them and `apply` writes them. `check` reads the old workflows and returns a `Gap` for every step that fastci does not cover.

## The rules a change must keep

- **The plan is pure.** `build` takes everything it needs as arguments and runs nothing, so every tier's plan is tested without git, CI or pytest.
- **Recording never gates.** A slower suite under coverage must never fail a merge, so the map job is separate and allowed to fail.
- **The default branch runs everything.** Selection only ever narrows a pull request or a laptop run. A miss is caught on the next merge, never shipped.
