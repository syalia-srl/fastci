# fastci

fastci chooses which pytest tests a pull request runs. It records, on main,
what every test executes and reads, and in a pull request it picks the tests
that executed the changed lines or read the changed files. It exists so that a
pull request is green in minutes and the full suite runs once, on main. It is a
developer tool for the repositories built with aegis, public, MIT.

This file changes when the goals change. Nothing in it should be made false by a
commit that adds code or content.

## What done means

A change is done when its tests pass, `ruff check` and `ruff format --check`
are clean, and a rule change comes with a fixture case that goes red without it.
A rule that loses a test a real pull request needed is a regression, even if it
saves time: recall comes before speed.

## Where everything lives

| What | Where |
|---|---|
| Why it is built this way, and what it will not do | `DESIGN.md` |
| What it is and how to use it | `README.md` |
| What changed in each version | `CHANGELOG.md` |

## Working here

```bash
uv sync --locked
uv run pytest -q
uv run ruff check src tests && uv run ruff format --check src tests
```
