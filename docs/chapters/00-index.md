fastci is one command that runs a repository's checks and tests the same way on a laptop, in a pull request, on the default branch and on a release branch. It works out which of those cases it is in, runs the cheapest checks first, stops at the first failure, and runs only the tests the change can break.

It exists because a test suite that runs whole on every push costs time twice: on the laptop, where the person waits for it, and in CI, where a pull request waits in a queue. Most of those tests cannot fail on a given change. fastci keeps a map of what each test runs, recorded once on the default branch, and from a diff it picks the tests that ran the changed lines or read the changed files. The default branch still runs everything, so what a pull request misses shows up on the next merge.

What fastci has:

- one command, `fastci`, whose plan depends on where it runs, and `fastci plan`, which prints that plan and why;
- tiers, each a table in the configuration, with defaults for a laptop, a pull request, the default branch and a release;
- the map, recorded by `fastci map record`, and the rules that turn a diff into tests;
- caps for a laptop: workers sized from free memory, a memory ceiling, a low priority;
- `fastci setup`, which proposes the configuration and the CI workflow for a repository, and `fastci guide`, which tells an agent how to move a repository's CI to fastci.

Python projects with pytest are supported today. Other languages plug in as adapters (the last chapter).

The book is also a [PDF](fastci.pdf).

The [Quick Start](quick-start.html) runs fastci on a small project in a few minutes. The User Guide then follows the jobs you will do with it: moving a repository onto it, configuring its tiers, reading why a test ran, adding checks of your own, and keeping a laptop responsive. The Developer Guide explains how fastci is built and how a new language joins it.
