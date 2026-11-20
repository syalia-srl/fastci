# Quick Start

fastci is a tool of its own, so nothing goes into your project. It is not on PyPI yet, so install it from its git tag:

```bash
uv tool install git+https://github.com/syalia-srl/fastci@v0.2.1
fastci --version
```

In CI the generated workflow runs it with `uvx --from git+…` instead. On a laptop, install it: `uvx` resolves the git tag again on every call, about seven seconds each time, while the installed command plans in a fraction of a second.

This chapter uses a small project, a shop with a cart. It has two functions and one test for each, and `total` reads its prices from a JSON file:

```{python echo=false output=false}
from bookkit import Demo

demo = Demo()
```

```{python continue}
demo.show("src/shop/cart.py")
```

Run `fastci plan` in a repository to see what fastci would run there, without running it:

```{python continue}
demo.sh("fastci plan")
```

The first line says where fastci thinks it is: no `CI` variable, so a laptop, the `local` tier, and the change is the diff against `origin/main`. Nothing has changed yet and there is no map, so fastci has no test to run besides the ones marked `always`. The last line shows the limits it would put on the run, which [a later chapter](on-a-laptop.html) explains.

## The map

To pick tests, fastci needs to know what each test runs. `fastci map record` runs the suite once and writes that down. In CI the default branch records it after every merge, and pull requests and laptops download it. Here we record it by hand:

```{python continue}
demo.sh("fastci map record")
```

## A change

Now change `discount`, the way a pull request would:

```{python continue}
demo.edit("src/shop/cart.py", "(1 - pct / 100)", "(100 - pct) / 100")
demo.sh("fastci plan")
```

One test of two is picked: the one that ran the changed line. `fastci` runs the plan:

```{python continue}
demo.sh("fastci")
```

That is the whole loop. On a laptop you run `fastci` before you push. In CI the workflow that [`fastci setup`](moving-a-repository-to-fastci.html) writes runs the same command, and fastci reads from the environment which tier it is in.
