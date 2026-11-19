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
    import shutil

    from .config import load
    from .tier import detect

    root = _root()
    cfg = load(root)
    ctx = detect(os.environ, cfg.default_branch, args.tier)
    if args.base and ctx.tier in ("T0", "T1"):
        ctx = dataclasses.replace(ctx, base=args.base)
    return root, cfg, ctx, shutil.which("nvidia-smi") is not None


def _plan(args) -> int:
    from .plan import build, gather, render

    root, cfg, ctx, gpu = _context(args)
    projects, changed, picks, maps = gather(root, ctx, cfg, os.path.join(root, args.map))
    print(render(ctx, build(ctx, cfg, root, projects, changed, picks, maps, gpu)))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    own, rest = _split(argv)
    parser = argparse.ArgumentParser(prog="fastci", description=__doc__)
    top = parser.add_subparsers(dest="top", required=True)
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

    args = parser.parse_args(own)

    if args.top == "plan":
        return _plan(args)

    if args.cmd == "record":
        from .python.record import default_sources, record

        root = _root()
        return record(rest, args.out, args.source or default_sources(root), root)

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
