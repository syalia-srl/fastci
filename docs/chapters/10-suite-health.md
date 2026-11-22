# The suite's health

A test suite gets slower and less useful one test at a time. A fixture that builds a database for every test, two tests that check the same path, a module no test touches: none of these fails a run, so none of them gets fixed. `fastci report` lists them, with numbers, from the map that the default branch already records. It runs no test, so you can make it again in seconds, at any commit the branch has a map for.

The report is written first for an agent that is about to refactor a suite before a release, and second for a person: the same data comes out as text, as JSON and as one HTML page.

## A suite with problems

The shop from the Quick Start, with three problems added: a fixture that sleeps for every test that uses it, two tests in different files that run the same receipt code, and a module no test imports.

```{python echo=false output=false}
from bookkit import Demo

demo = Demo()
lines = "\n".join(f"    line{i} = f'{{name}} x{{qty}}'" for i in range(16))
demo.write("src/shop/receipt.py", "def receipt(name, qty):\n" + lines + "\n    return line0\n")
demo.write("src/shop/legacy.py", "def old_total(items):\n    return sum(items)\n")
demo.write(
    "tests/test_receipt.py",
    """\
    from shop.receipt import receipt


    def test_receipt_names_the_item():
        assert receipt("apple", 2).startswith("apple")
    """,
)
demo.write(
    "tests/test_receipt_again.py",
    """\
    from shop.receipt import receipt


    def test_receipt_counts():
        assert "x2" in receipt("apple", 2)
    """,
)
demo.write(
    "tests/test_catalog.py",
    """\
    import time

    import pytest


    @pytest.fixture
    def catalog():
        time.sleep(0.2)
        return {"apple": 2}


    def test_has_apple(catalog):
        assert "apple" in catalog


    def test_apple_price(catalog):
        assert catalog["apple"] == 2


    def test_no_pear(catalog):
        assert "pear" not in catalog
    """,
)
demo.edit("pyproject.toml", "[tool.pytest.ini_options]", "[tool.fastci.flaky]\nreach-share = 0.4\n\n[tool.pytest.ini_options]")
demo.commit("receipts, a catalog and old code", merge=True)
```

The shop has only seven tests, so the two receipt tests are more than a fifth of the suite, and by the default rule below their lines would count as shared by everyone. Its `pyproject.toml` raises the share to 0.4 under `[tool.fastci.flaky]`. A real suite keeps the default.

Record the map, as the default branch does after every merge, then ask for the report:

```{python continue}
demo.sh("fastci map record")
demo.sh("fastci report --top 3")
```

Read it from the top.

- The header says what the map cannot see: the pytest arguments it was recorded with, the tests deselected or skipped then, the tests that started a child process, and files that are not Python. A finding that one of these explains carries a tag, such as `spawns` or `outside the lane`.
- **Time** comes first because it is where the evaluation behind this chapter found most of the real problems. *Expensive set-up* ranks fixtures by the seconds all their runs cost: `catalog` runs once per test, so three tests pay it three times. A module, class, package or session fixture that runs more than once on the same worker for the same scope is counted as rebuilt. *Much time, little code* lists tests whose call takes long over very few lines run: a wait on a real clock, work in a child process, or work in code outside the sources. *Time by test file* shows each file's floor, its fastest test times its number of tests: a high floor means every test in the file pays the same set-up.
- **Overlap** reads each test's own lines: the lines it ran that at most `reach-share` of the suite ran (a fifth by default, in `[tool.fastci.flaky]`). *Same code path* groups tests whose own lines are identical, at least 15 of them. The two receipt tests are such a group. The cases of one parametrized test are one entry, and a group made only of one test's cases is left out, since parametrizing is already done. *Almost the same* lists clusters inside one test file whose own lines overlap by 90% or more. *Start-up path* lists the functions most of the suite runs.
- **Coverage** counts a line as run when a test ran it or it ran at import, during collection. Each file lists the functions whose body no test entered. *Never imported* tells modules from scripts (a `__main__` block, a `scripts/`, `tools/` or `bin/` directory, or a file the Makefile or a document names).
- **Flaky** lists the tests that failed and then passed on the default branch, from the history the [flaky tests](flaky-tests.html) chapter describes.

Overlap cannot see what a test asserts. The two receipt tests run the same lines and check different things; whether to merge them is a decision for whoever reads the tests. The report gives the evidence and never a verdict, and it never fails a run.

## JSON and HTML

`fastci report --json` prints the whole model: every entry of every section, where the text shows only `--top` of each. Its top-level keys are `version`, `header`, `time`, `overlap`, `coverage` and `flaky`, and every section is `{"entries": [...], "total": {...}, "note": ...}`. A note replaces a section's entries when the map cannot fill it, for example a map recorded by an older fastci.

`fastci report --html report.html` also writes one page with the same sections as sortable, filterable tables. It needs no network and reads on a phone.

## Where the maps come from

Without `--map`, the report downloads the newest map of the default branch, and with `--maps 5` the five newest, taking each test's median duration across them. It needs `GH_TOKEN` or `GITHUB_TOKEN`, and finds the repository from `--repo`, `GITHUB_REPOSITORY` or the `origin` remote. Without a token it reads `.fastci/map.json.gz` if one is there. `--flaky` reads a local flaky history instead of the branch's.

Durations in a map were recorded under coverage, which slows Python code unevenly; the order of the tests is what to trust, more than the seconds.

## Running it before every release

A repository that wants the report in its release run adds it as a stage of the release tier:

```toml
[tool.fastci.stages]
health = { run = "fastci report --top 20", tiers = ["release"] }
```

The stage prints the report and always passes.

## How it is built

`fastci.report` holds a `Suite`, the newest map with what the sections derive from it (each test's own lines, the median durations), and `build`, which turns a `Suite` and the flaky history into the model. The sections live in `time`, `overlap` and `coverage`, and `render` prints the model as text, JSON or HTML. The map's version 3 fields that feed them are recorded by the plugin and the recorder; the [architecture](how-fastci-is-built.html) chapter describes those.
