# Stages: checks of your own

Before any test, fastci runs the project's checks, cheapest first: the lock file, the formatter, the linter, the type checker. A formatting mistake costs a second to find and should never wait behind a five-minute suite. The first red stage ends the run, and the stages after it are named as not run.

The checks fastci knows come from what the project uses: `uv lock --check` when there is a `uv.lock`, `ruff format --check` and `ruff check` when ruff is a dev dependency or configured, and the first of `ty`, `mypy` or `pyright` it finds. On a laptop and in a pull request a check whose files did not change is skipped.

```{python echo=false output=false}
from bookkit import Demo

demo = Demo()
demo.edit("pyproject.toml", 'dev = ["pytest"]', 'dev = ["pytest", "ruff"]')
demo._run("uv", "sync", "-q")
demo.commit("ruff", merge=True)
```

The shop now has ruff, so the plan for the default branch has format and lint checks:

```{python continue}
demo.sh("fastci plan --tier main")
```

## Replacing, adding, turning off

`[tool.fastci.stages]` changes the checks. A string replaces a check's command, an empty string turns it off, and a new name adds a stage. A table also says in which tiers the stage runs and after which stage it goes:

```{python continue}
demo.edit(
    "pyproject.toml",
    "[tool.pytest.ini_options]",
    """[tool.fastci.stages]
lint = "uv run ruff check --select E,F"
docs = { run = "test -f README.md", tiers = ["pr", "main"], after = "lint" }

[tool.pytest.ini_options]""",
)
demo.sh("fastci plan --tier main")
```

A stage is a plain shell command, so anything a repository already checks fits: a docs build, a schema check, a script that compares a generated file. A stage listed for tiers that do not exist is an error that names them, so a typo cannot silently turn a stage off.

## Fixing what can be fixed

When the formatter or the linter fails, `fastci fix` runs their fixing commands (`ruff format`, `ruff check --fix`) and then the checks again:

```{python continue}
demo.write("src/shop/coupon.py", "def code( name ):\n    return name.upper( )\n")
demo.sh("fastci")
```

```{python continue}
demo.sh("fastci fix")
demo.show("src/shop/coupon.py")
```
