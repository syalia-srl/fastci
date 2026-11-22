"""The report's model printed for an agent (text, JSON) and for a person (HTML)."""

from __future__ import annotations

import json

# (group, key, title, what the section points to)
SECTIONS = [
    (
        "time",
        "setup_cost",
        "Expensive set-up",
        "widen the fixture's scope, or build its data once and copy it",
    ),
    (
        "time",
        "slowest",
        "Slowest tests",
        "split the test, mark it slow, shrink its input, or move work out of it",
    ),
    (
        "time",
        "little_code",
        "Much time, little code",
        "a fake clock, an in-process call, or a smaller input",
    ),
    (
        "time",
        "by_test_file",
        "Time by test file",
        "a high floor means every test in the file pays the same set-up",
    ),
    ("time", "always_cost", "Cost of always", "every pull request pays these"),
    (
        "overlap",
        "same_path",
        "Same code path",
        "parametrize them, merge them, or share one fixture; read the assertions first",
    ),
    (
        "overlap",
        "near_same",
        "Almost the same",
        "parametrize or merge them; what each adds to the common core is what differs",
    ),
    ("overlap", "startup_path", "Start-up path", "call the unit directly, or share the booted app"),
    ("coverage", "by_file", "Coverage by file", "where a missing test would cover the most code"),
    (
        "coverage",
        "never_imported",
        "Never imported",
        "dead code, or a script that needs a smoke test",
    ),
    ("flaky", None, "Flaky on main", "fix the race or the order dependence"),
]


def as_json(model: dict) -> str:
    return json.dumps(model, indent=1, ensure_ascii=False)


def _s(x: float) -> str:
    return f"{x:.2f} s"


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _entry(key: str, e: dict) -> list[str]:
    if key == "setup_cost":
        rebuilt = f", rebuilt {e['rebuilt']}x" if e["rebuilt"] else ""
        return [
            f"{_s(e['seconds'])}  {e['fixture']} ({e['scope']}, {e['defined_in'] or 'root'})"
            f": {e['runs']} runs for {e['tests']} tests{rebuilt}"
        ]
    if key == "slowest":
        cases = f" ({e['cases']} cases)" if e["cases"] > 1 else ""
        marks = f" [{', '.join(e['marks'])}]" if e["marks"] else ""
        return [
            f"{_s(e['seconds'])}  {_pct(e['share'])}  set-up {e['setup']:.2f} / call "
            f"{e['call']:.2f} / teardown {e['teardown']:.2f}  {e['test']}{cases}{marks}"
        ]
    if key == "little_code":
        sp = ", started a child process" if e["spawns"] else ""
        return [f"{_s(e['call'])} in call over {e['lines']} lines run{sp}  {e['test']}"]
    if key == "by_test_file":
        return [
            f"{_s(e['seconds'])}  {_pct(e['share'])}  floor {_s(e['floor'])}  "
            f"{e['tests']} tests  {e['file']}"
        ]
    if key == "always_cost":
        return [f"{_s(e['seconds'])}  {e['test']}"]
    if key == "same_path":
        fx = f", all request {', '.join(e['fixtures'])}" if e["fixtures"] else ""
        head = (
            f"{len(e['tests'])} tests, {_s(e['seconds'])}, {_s(e['beyond_fastest'])} beyond the "
            f"fastest, {e['files']} file{'s' if e['files'] > 1 else ''}, "
            f"{e['own_lines']} own lines{fx}"
        )
        return [head] + [
            f"    {t['test']}" + (f" ({t['cases']} cases)" if t["cases"] > 1 else "")
            for t in e["tests"]
        ]
    if key == "near_same":
        head = (
            f"{len(e['tests'])} tests, {_s(e['seconds'])}, {e['core']} lines in common  {e['file']}"
        )
        return [head] + [f"    +{t['adds']:<4} {t['test']}" for t in e["tests"]]
    if key == "startup_path":
        head = (
            f"{e['tests']} tests, {_s(e['seconds'])}, median {e['median']:.3f} s, "
            f"{len(e['functions'])} functions"
        )
        return (
            [head]
            + [f"    {f}" for f in e["functions"][:5]]
            + ([f"    ... {len(e['functions']) - 5} more"] if len(e["functions"]) > 5 else [])
        )
    if key == "by_file":
        tags = f" [{', '.join(e['tags'])}]" if e["tags"] else ""
        head = (
            f"{e['percent']:5.1f}%  {e['run']}/{e['lines']} lines, {e['tests']} tests  "
            f"{e['file']}{tags}"
        )
        if e["untested"] is None:
            return [head + "  (changed since the map)"]
        return [head] + [
            f"    untested: {u['function']} ({u['lines']} lines)" for u in e["untested"][:5]
        ]
    if key == "never_imported":
        kind = "script" if e["script"] else "module"
        return [f"{e['lines']:5} lines  {kind}  {e['file']}"]
    if key is None:  # flaky
        flag = f", flagged #{e['issue']}" if e["issue"] else ""
        return [f"{e['failures']}/{e['runs']} runs failed ({_pct(e['rate'])}){flag}  {e['test']}"]
    return [str(e)]


def _total(key: str, sec: dict) -> str:
    t, n = sec["total"], len(sec["entries"])
    if key in ("same_path", "near_same", "startup_path"):
        extra = (
            f", {_s(t['beyond_fastest'])} beyond the fastest of each"
            if "beyond_fastest" in t
            else ""
        )
        return (
            f"{n} found{extra}; own lines leave out {t['shared_lines']} lines run by more "
            f"than {_pct(t['reach_share'])} of the suite"
        )
    if key == "by_file":
        return f"{t['percent']}% of {t['lines']} executable lines run, {n} files"
    if key == "slowest":
        return f"{_s(t['seconds'])} in all; the top 10 take {_pct(t['top10_share'])}"
    if "seconds" in t:
        return f"{n} found, {_s(t['seconds'])}"
    return f"{n} found"


def _header(h: dict) -> list[str]:
    sha = (h.get("sha") or "")[:10] or "unknown commit"
    date = f" ({h['date'][:10]})" if h.get("date") else ""
    out = [
        f"fastci report · map {sha}{date} · {h['tests']} tests · {_s(h['seconds'])} recorded",
        "",
    ]
    if h.get("head_differs"):
        out.append("! this checkout is not the map's commit: line numbers may have moved")
    if h["args"]:
        out.append(f"recorded with: {' '.join(h['args'])}")
    out.append(
        f"deselected when recorded: {h['deselected']}; started a child process: "
        f"{h['spawns']}; maps read: {h['maps']}"
    )
    for s in h["skipped"]:
        always = ", on every map" if s["every_map"] and h["maps"] > 1 else ""
        out.append(f"skipped: {s['test']} ({s['reason']}{always})")
    for f in h["unparsed"]:
        out.append(f"not Python: {f}")
    out += [f"note: {n}" for n in h["notes"]]
    return out


def text(model: dict, top: int) -> str:
    out = _header(model["header"])
    for group, key, title, points in SECTIONS:
        sec = model[group] if key is None else model[group].get(key, {})
        out += ["", f"== {title}"]
        if sec.get("note"):
            out.append(sec["note"])
            continue
        out.append(_total(key, sec))
        out.append(f"points to: {points}")
        for e in sec["entries"][:top]:
            out += _entry(key, e)
    return "\n".join(out) + "\n"
