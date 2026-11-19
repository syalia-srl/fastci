"""The ``fastci`` command."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

DEFAULT_MAP = ".fastci/map.json.gz"


def _root() -> str:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return out.stdout.strip() or os.getcwd()


def _split(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def _pick(args):
    from .config import load
    from .python.mapfile import TestMap
    from .python.pick import Picker, git

    root = _root()
    if not os.path.exists(args.map):
        print(f"fastci: no map at {args.map}; every test runs", file=sys.stderr)
        return None, None
    tm = TestMap.load(args.map)
    base = git(root, "merge-base", args.base, args.head or "HEAD").strip() or args.base
    p = Picker(tm, root, load(root).tests).pick(base, args.head)
    if p.full:
        print(f"fastci: {p.why['*']}; every test runs", file=sys.stderr)
        return tm, p
    share = sum(tm.durations[i] for i, t in enumerate(tm.tests) if t in p.tests)
    total = sum(tm.durations) or 1
    print(
        f"fastci: {len(p.tests)} of {len(tm.tests)} tests from the map"
        f" ({share / total:.0%} of the suite's time) and {len(p.files)} changed test files",
        file=sys.stderr,
    )
    return tm, p


def _context(args):
    import dataclasses

    from . import local
    from .config import load
    from .tier import detect

    root = _root()
    cfg = load(root)
    ctx = detect(os.environ, cfg.default_branch, args.tier)
    if args.base and ctx.tier in ("T0", "T1"):
        ctx = dataclasses.replace(ctx, base=args.base)
    return root, cfg, ctx, local.has_gpu()


def _plan(args) -> int:
    from .plan import render

    root, cfg, ctx, projects, steps, caps = _prepare(args)
    print(render(ctx, steps))
    if caps["line"]:
        print(caps["line"])
    return 0


def _prepare(args):
    import dataclasses

    from .execute import ensure_map, repo_of
    from .plan import build, gather

    root, cfg, ctx, gpu = _context(args)
    map_path = os.path.join(root, args.map)
    if args.map == DEFAULT_MAP:
        map_path = ensure_map(root, ctx, repo_of(root), cfg.default_branch) or map_path
    projects, changed, picks, maps = gather(root, ctx, cfg, map_path)
    steps = build(ctx, cfg, root, projects, changed, picks, maps, gpu)
    caps = _caps(cfg, ctx, projects, maps)
    if caps["workers"]:
        n = ["-n", str(caps["workers"])]
        steps = [
            dataclasses.replace(s, command=[*s.command, *n])
            if s.action == "run" and s.stage in ("tests", "release")
            else s
            for s in steps
        ]
    return root, cfg, ctx, projects, steps, caps


def _caps(cfg, ctx, projects, maps) -> dict:
    """On a laptop: pytest workers, the memory cap and the wrapper that applies them."""
    from . import local

    none = {"workers": 0, "wrap": lambda a: a, "note": None, "line": None}
    if ctx.tier != "T0":
        return none
    avail = local.mem_available()
    systemd = local.has_systemd_user()
    share = cfg.local.memory
    workers = 0
    if any("xdist" in p.tools for p in projects):
        peak = max((m.peak_rss for m in maps.values() if m), default=0) or local.GIB
        workers = cfg.local.workers or (
            local.workers(os.cpu_count() or 2, avail, share, peak) if avail else 1
        )
    cap = f"{avail * share / local.GIB:.1f} GiB" if avail and systemd else None
    line = f"laptop: {workers or 'no'} pytest workers, " + (
        f"memory capped at {cap} with systemd-run, nice 10"
        if cap
        else "no memory cap (no systemd user session), nice 10"
    )
    return {
        "workers": workers,
        "wrap": lambda a: local.wrap(a, avail, share, systemd),
        "note": f"the run hit the memory cap of {cap}; raise [tool.fastci.local] memory"
        " or lower workers"
        if cap
        else None,
        "line": line,
    }


def _run(args) -> int:
    from .adapter import adapters
    from .execute import execute, say

    root, cfg, ctx, projects, steps, caps = _prepare(args)
    say(ctx.reason)
    if caps["line"]:
        say(caps["line"])
    if not steps:
        say("nothing to check here: no project with checks or tests was found")
        return 0
    return execute(
        steps,
        root,
        {a.name: a for a in adapters()},
        {p.root: p for p in projects},
        fail_fast_tests=ctx.tier == "T0" and cfg.local.fail_fast,
        wrap=caps["wrap"],
        cap_note=caps["note"],
    )


def _fix(args) -> int:
    import subprocess

    from .adapter import adapters
    from .execute import execute, say

    args.tier = "T0"
    root, cfg, ctx, projects, steps, caps = _prepare(args)
    by_root = {p.root: p for p in projects}
    for s in steps:
        if s.action != "run":
            continue
        fix = next((c.fix for c in by_root[s.project].checks if c.name == s.stage), None)
        if fix:
            say(f"{s.stage}: {fix}")
            subprocess.run(["sh", "-c", fix], cwd=os.path.join(root, s.project))
    checks = [s for s in steps if s.stage not in ("always", "tests", "release")]
    return execute(checks, root, {a.name: a for a in adapters()}, by_root, False)


def _setup(args) -> int:
    from . import setup

    root = _root()
    if args.check:
        gaps = setup.check(root)
        for g in gaps:
            print(f"{g.workflow} · {g.job} · {g.step}: {g.run}")
        print(f"{len(gaps)} steps not covered" if gaps else "every step is covered")
        return 1 if gaps else 0
    changes = setup.propose(root, args.from_map)
    print(setup.detect_summary(root))
    if args.diff:
        print(setup.diff(changes), end="")
    else:
        print("\n".join(setup.summary_lines(changes)) or "nothing to change")
    if args.write:
        setup.apply(root, changes)
        print(f"wrote {len(changes)} changes")
    elif changes and not args.diff:
        print("\nrun `fastci setup --write` to apply, `fastci setup --diff` to see each change")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    own, rest = _split(argv)
    parser = argparse.ArgumentParser(prog="fastci", description=__doc__)
    parser.add_argument("--tier", choices=["T0", "T1", "T2", "T3"], help="override the tier")
    parser.add_argument("--base", help="what the change is compared to (T0 and T1)")
    parser.add_argument("--map", default=DEFAULT_MAP)
    top = parser.add_subparsers(dest="top")
    top.add_parser("fix", help="rewrite what the checks can fix (format, lint), then check")
    mp = top.add_parser("map", help="record, read and fetch the map of what each test runs")
    sub = mp.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="run pytest once and write the map")
    rec.add_argument("--out", default=DEFAULT_MAP)
    rec.add_argument("--source", action="append", help="code to map (default: src, or the repo)")

    for name, hlp in (
        ("pick", "print the tests a diff can break"),
        ("run", "run pytest on the tests a diff can break"),
    ):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--base", default="origin/main", help="what the change is compared to")
        p.add_argument("--head", default=None, help="the change's commit (default: working tree)")
        p.add_argument("--map", default=DEFAULT_MAP)
        if name == "pick":
            p.add_argument("--why", action="store_true", help="say why each test was picked")

    fe = sub.add_parser("fetch", help="download the newest map a branch's CI uploaded")
    fe.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), required=False)
    fe.add_argument("--branch", default="main")
    fe.add_argument("--name", default="fastci", help="the artifact's name")
    fe.add_argument("--out", default=DEFAULT_MAP)

    pl = top.add_parser("plan", help="print what fastci would run here, and why")
    pl.add_argument("--tier", choices=["T0", "T1", "T2", "T3"], help="override the detected tier")
    pl.add_argument("--base", help="what the change is compared to (T0 and T1)")
    pl.add_argument("--map", default=DEFAULT_MAP)

    st = top.add_parser("setup", help="propose the config, workflow and test marks")
    st.add_argument("--diff", action="store_true", help="show each change as a diff")
    st.add_argument("--write", action="store_true", help="apply the changes")
    st.add_argument("--from-map", help="also mark tests slower than slow-seconds in this map")
    st.add_argument(
        "--check", action="store_true", help="list old workflow steps fastci does not cover"
    )
    top.add_parser("guide", help="print how to move a repository's CI to fastci")

    args = parser.parse_args(own)

    if args.top == "plan":
        return _plan(args)
    if args.top is None:
        return _run(args)
    if args.top == "fix":
        return _fix(args)
    if args.top == "setup":
        return _setup(args)
    if args.top == "guide":
        from importlib.resources import files

        print((files("fastci") / "guide.md").read_text(), end="")
        return 0

    if args.cmd == "record":
        from .python.adapter import PythonAdapter
        from .python.record import default_sources, record

        root = _root()
        if args.source:
            return record(rest, args.out, args.source, root)
        adapter = PythonAdapter()
        projects = adapter.detect(root)
        if not projects:
            return record(rest, args.out, default_sources(root), root)
        return adapter.record(projects[0], root, os.path.abspath(args.out), rest)

    if args.cmd == "fetch":
        from .fetch import fetch

        if not args.repo:
            parser.error("--repo is required outside GitHub Actions")
        sha = fetch(args.repo, args.out, args.branch, args.name)
        if sha is None:
            print(
                f"fastci: no '{args.name}' artifact from {args.branch} in {args.repo}",
                file=sys.stderr,
            )
            return 0
        print(f"fastci: map of {args.branch} at {sha[:10]} written to {args.out}", file=sys.stderr)
        return 0

    _, p = _pick(args)
    if args.cmd == "pick":
        if p is None or p.full:
            print("ALL")
        else:
            for line in p.lines():
                print(f"{line}\t{p.why.get(line, '')}" if args.why else line)
        return 0

    cmd = [sys.executable, "-m", "pytest", *rest]
    env = dict(os.environ)
    if p is not None and not p.full:
        if not p.lines():
            print("fastci: no test can be affected by this change; nothing to run", file=sys.stderr)
            return 0
        fd, sel = tempfile.mkstemp(prefix="fastci-select-", suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(p.lines()) + "\n")
        env["FASTCI_SELECT"] = sel
        cmd[3:3] = ["-p", "fastci.python.plugin"]
    code = subprocess.run(cmd, env=env).returncode
    return 0 if code == 5 else code


if __name__ == "__main__":
    sys.exit(main())
