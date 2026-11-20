# On a laptop

A test suite run on a laptop competes with the editor, the browser and whatever else is open. Run with all cores and no limit, a large suite can freeze the machine or push it into swap, and a test that leaks memory can take the session down. In the `local` tier fastci limits the run so the laptop stays usable:

- **Workers.** With pytest-xdist installed, fastci runs min(cores − 1, free memory × share ÷ memory per worker) workers. Memory per worker is the peak the map recorded, or 1 GB without a map.
- **Memory.** On Linux the whole run goes into a systemd user scope with `MemoryMax` set to the share of available memory, and swap turned off for it. If the run hits the ceiling, fastci says so and names the setting to change. Where systemd is missing it runs without a ceiling and says so.
- **Priority.** The run gets `nice 10`, so the editor and the browser stay responsive.
- **Fail fast.** pytest stops at the first failing test, and the first red stage ends the run.
- **Nothing slow.** The release-only marks never run here, and `gpu` tests never run on a machine without a GPU.

```{python echo=false output=false}
from bookkit import Demo

demo = Demo()
```

The last line of every plan on a laptop shows the limits:

```{python continue}
demo.sh("fastci plan")
```

The settings are in `[tool.fastci.local]`:

```toml
[tool.fastci.local]
memory = "50%"    # share of available memory for the whole run
workers = "auto"  # or a number
```

To report every failing test instead of the first, turn fail-fast off for the tier:

```toml
[tool.fastci.tiers.local]
fail-fast = false
```

On a laptop fastci downloads the newest map from the default branch's CI, using the token of `gh` when you are signed in. It gives up after five seconds, so a laptop without network still gets its checks at once.
