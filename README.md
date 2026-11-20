# fastci

Run, in each case, only the checks and tests a change can break, cheapest
first, and stop at the first failure.

fastci was called testmap up to 0.1.0. It is not on PyPI yet, so it installs from
its git tag:

```bash
uv tool install git+https://github.com/syalia-srl/fastci@v0.2.1   # once; CI uses uvx
fastci setup          # propose the config, the workflow and the test marks
fastci setup --write  # apply them
fastci                # run what this case needs
fastci plan           # print what it would run, and why
fastci guide          # how to move a repository's CI to fastci
```

**The book** is at <https://syalia.com/fastci/>: a Quick Start, a
User Guide (moving a repository, tiers, why a test ran, stages, the laptop) and
a Developer Guide. Every command in it runs when the book is built.

## Limits

Python and pytest only in this version; JavaScript and TypeScript, path suites
for other languages, and browser and end-to-end suites come next. Code that a
test reaches through a non-Python process (a shell script, a Node server) is
linked to the test only when the test opens the file itself or names it in its
source.
