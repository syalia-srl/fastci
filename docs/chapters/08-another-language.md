# Adding a language

Everything fastci knows about Python lives in one class, `PythonAdapter`. The core asks it four things through the `Adapter` protocol in `fastci.adapter`, and a new language answers the same four:

- `detect(repo)` returns the `Project`s in the repository, with the tools found and the checks they bring;
- `run_tests(project, repo, select, args, env, wrap)` runs the project's tests, all of them or only `select`, through `wrap` so the laptop's limits apply, and returns the exit code;
- `record(project, repo, out, args)` runs the suite once and writes the map to `out`;
- `tests(project, repo)` lists the project's tests and the marks of each, which `fastci setup` reads.

Here is the smallest adapter that works: a project with a `Makefile` and a `test` target. It has no checks of its own, cannot select tests and records no map, so the plan runs every test in every tier:

```{python echo=false output=false}
import os

from bookkit import CLEAN, Demo

demo = Demo()
demo.write("Makefile", "test:\n\t@echo all good\n")
os.chdir(demo.root)
os.environ.clear()
os.environ.update(CLEAN)
```

```{python continue}
import subprocess
from pathlib import Path

from fastci.adapter import Project


class MakeAdapter:
    name = "make"

    def detect(self, repo):
        if not (Path(repo) / "Makefile").exists():
            return []
        return [Project("make", ".", frozenset({"make"}), ())]

    def run_tests(self, project, repo, select, args, env, wrap=lambda a: a):
        return subprocess.run(wrap(["make", "test", *args]), cwd=repo, env=env).returncode

    def record(self, project, repo, out, args):
        return 0

    def tests(self, project, repo):
        return []
```

The plan treats its projects like any other:

```{python continue}
from fastci.config import load
from fastci.plan import build, render
from fastci.tier import detect

cfg = load(".")
ctx = detect(os.environ, cfg.default_branch, cfg.tiers, "main")
projects = MakeAdapter().detect(".")
print(projects)
steps = build(ctx, cfg, ".", projects, None, {".": None}, {".": None}, gpu=False)
print(render(ctx, steps))
```

The plan is empty because test stages are built only for projects whose tools include `pytest`. That is the first thing part 2 of fastci changes: the stages a project gets will come from its adapter. Until then a new adapter is registered in `fastci.adapter.adapters`, and its tests run through `[tool.fastci.stages]`, as a stage whose command is the project's own test runner.

## Speaking JSON

Adapters for other languages will not be Python classes. The same four calls will go to a program, as one JSON object on its stdin and one on its stdout, so the JavaScript adapter can be written in JavaScript and run its tests where its tools are. `PythonAdapter` stays in-process because its recorder must run inside the Python process anyway.
