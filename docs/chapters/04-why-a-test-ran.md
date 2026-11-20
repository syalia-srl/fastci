# Why a test ran

When a pull request runs a test you did not expect, or skips one you did, you want to know why. The answer is always in two places: what the map recorded about the test, and which rule turned your diff into a pick.

```{python echo=false output=false}
from bookkit import Demo

demo = Demo()
```

## What the map records

`fastci map record` runs the suite once under coverage. For every test it writes down the lines of your code the test ran, including code in subprocesses the test started, and the files of the repository it opened for reading. It also stores each test's duration and the memory the run peaked at.

```{python continue}
demo.sh("fastci map record")
```

The shop's map has two tests, one source file and one file read: `test_total` opened `prices.json` through `total`.

## From a diff to tests

`fastci map pick` prints the tests a diff can break, and `--why` adds the reason for each. Change the prices:

```{python continue}
demo.edit("src/shop/prices.json", '"bread": 3', '"bread": 4')
demo.sh("fastci map pick --base origin/main --why")
```

`test_total` is picked because it read the file. `test_discount` never opened it, so it is left out. The rules, for every path the diff touches:

| The diff changes… | fastci picks… |
|---|---|
| the lockfile, `pyproject.toml`, a root `conftest.py`, a workflow | every test |
| a test file | that file |
| a line inside a function | the tests that ran that line; for a new line, the tests that ran the function |
| a line at module level | the tests that ran any of that file; for an import, the fastest one |
| a blank line, a comment, a docstring | nothing |
| a file some test read (CSS, JSON, a script) | the tests that read it, and the tests whose source names it |

A comment changes nothing a test can see, so it picks nothing:

```{python continue}
demo.edit("src/shop/prices.json", '"bread": 4', '"bread": 3')
demo.edit(
    "src/shop/cart.py",
    "def discount(price, pct):",
    "def discount(price, pct):\n    # pct is a percentage, 0 to 100",
)
demo.sh("fastci map pick --base origin/main --why")
```

## When a pick is wrong

The default branch runs the whole suite, so a test a pull request should have run and did not fails there, on the next merge. Fix the test, then fix the cause of the miss:

- **A file every test depends on without reading it**, such as a schema applied by a fixture outside the test: add its path to `[tool.fastci.tests] full`, and any change to it runs everything.
- **A coherence test**, one that compares a generated file with its source: mark it `@pytest.mark.always`. Tests marked `always` run in every tier, because the change that breaks them is the one that forgot to regenerate the file, which no rule can see.
- **A rule that should have picked the test**: open an issue on fastci with the change and the test.

## When everything is picked

In a repository where almost every test boots the same server, almost every function is run by almost every test, and line-level selection still picks most of the suite. `[tool.fastci.tests] global-share` narrows that. A unit of code that more than that share of the tests ran is narrowed to the tests that name it, plus the five fastest. It is off by default (`1.0`). On a large repository that boots a server per test, it cut the median pick from 90% to 65% of the suite's time. It also lost real failures where coherence tests were not marked `always`, so mark those first.
