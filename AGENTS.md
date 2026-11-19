# fastci

fastci runs, in each case (a laptop, a pull request, the default branch, a
release), only the checks and tests a change can break, cheapest first, and
stops at the first failure. For Python it records, on the default branch, what
every test executes and reads, and picks from that record. It exists so that a
pull request is green in minutes, a laptop is never flooded by a whole suite,
and the full suite runs once per merge. It is a public developer tool, MIT.

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
uv run fastci            # what this change needs, on this laptop
uv run fastci plan       # what it would run, and why
```
