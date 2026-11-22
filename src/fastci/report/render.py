"""The report's model printed for an agent (text, JSON) and for a person (HTML)."""

from __future__ import annotations

import html as _html
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
        phases = ""
        if e["setup"] is not None:
            phases = (
                f"  set-up {e['setup']:.2f} / call {e['call']:.2f} / teardown {e['teardown']:.2f}"
            )
        return [f"{_s(e['seconds'])}  {_pct(e['share'])}{phases}  {e['test']}{cases}{marks}"]
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


def _e(x) -> str:
    return _html.escape(str(x))


def _num(x, fmt="{:.2f}") -> str:
    return f'<td class="n" data-v="{x}">{_e(fmt.format(x))}</td>'


def _td(label: str, value: str, cls: str = "", sort=None) -> str:
    c = f' class="{cls}"' if cls else ""
    v = f' data-v="{sort}"' if sort is not None else ""
    return f'<td{c}{v} data-label="{_e(label)}">{value}</td>'


def _n(label: str, x, fmt: str = "{:.2f}") -> str:
    if x is None:
        return _td(label, "-", "n", -1)
    return _td(label, _e(fmt.format(x)), "n", x)


def _id(label: str, value: str) -> str:
    return _td(label, value, "id")


def _tags(tags) -> str:
    return "".join(f'<span class="tag">{_e(t)}</span>' for t in tags)


def _sub(text: str) -> str:
    return f'<br><span class="sub">{_e(text)}</span>'


def _cases(t: dict) -> str:
    n = t.get("cases", 1)
    return f' <span class="sub">({n} cases)</span>' if n > 1 else ""


def _row_setup_cost(e):
    return [
        _n("seconds", e["seconds"]),
        _id("fixture", _e(e["fixture"])),
        _td("scope", _e(e["scope"])),
        _td("defined in", _e(e["defined_in"] or "root")),
        _n("runs", e["runs"], "{}"),
        _n("tests", e["tests"], "{}"),
        _n("rebuilt", e["rebuilt"], "{}"),
    ]


def _row_slowest(e):
    return [
        _n("seconds", e["seconds"]),
        _n("share", 100 * e["share"], "{:.1f}%"),
        _n("set-up", e["setup"]),
        _n("call", e["call"]),
        _n("teardown", e["teardown"]),
        _id("test", _e(e["test"]) + _cases(e) + _tags(e["marks"])),
    ]


def _row_little_code(e):
    return [
        _n("call", e["call"]),
        _n("lines run", e["lines"], "{}"),
        _id("test", _e(e["test"]) + _tags(["spawns"] if e["spawns"] else [])),
    ]


def _row_by_test_file(e):
    return [
        _n("seconds", e["seconds"]),
        _n("share", 100 * e["share"], "{:.1f}%"),
        _n("floor", e["floor"]),
        _n("tests", e["tests"], "{}"),
        _id("file", _e(e["file"])),
    ]


def _row_always_cost(e):
    return [_n("seconds", e["seconds"]), _id("test", _e(e["test"]))]


def _row_same_path(e):
    tests = "<br>".join(_e(t["test"]) + _cases(t) for t in e["tests"])
    if e["fixtures"]:
        tests += _sub("all request " + ", ".join(e["fixtures"]))
    return [
        _n("beyond fastest", e["beyond_fastest"]),
        _n("seconds", e["seconds"]),
        _n("own lines", e["own_lines"], "{}"),
        _n("files", e["files"], "{}"),
        _id("tests", tests),
    ]


def _row_near_same(e):
    tests = "<br>".join(
        f'{_e(t["test"])} <span class="sub">+{t["adds"]}</span>' for t in e["tests"]
    )
    return [
        _n("seconds", e["seconds"]),
        _n("common lines", e["core"], "{}"),
        _id("file", _e(e["file"])),
        _id("tests", tests),
    ]


def _row_startup_path(e):
    return [
        _n("tests", e["tests"], "{}"),
        _n("seconds", e["seconds"]),
        _n("median", e["median"], "{:.3f}"),
        _id("functions", "<br>".join(_e(f) for f in e["functions"])),
    ]


def _row_by_file(e):
    width = max(2, round(e["percent"] * 0.6))
    lo = " lo" if e["percent"] < 50 else ""
    bar = f'<span class="bar{lo}" style="width:{width}px"></span>{e["percent"]}%'
    if e["untested"] is None:
        extra = _sub("changed since the map")
    else:
        extra = "".join(
            _sub(f"untested: {u['function']} ({u['lines']} lines)") for u in e["untested"]
        )
    return [
        _td("coverage", bar, "n", e["percent"]),
        _td("run / lines", f"{e['run']} / {e['lines']}", "n", e["lines"] - e["run"]),
        _n("tests", e["tests"], "{}"),
        _id("file", _e(e["file"]) + _tags(e["tags"]) + extra),
    ]


def _row_never_imported(e):
    return [
        _n("lines", e["lines"], "{}"),
        _td("kind", "script" if e["script"] else "module"),
        _id("file", _e(e["file"])),
    ]


def _row_flaky(e):
    return [
        _n("rate", 100 * e["rate"], "{:.1f}%"),
        _td("failed / runs", f"{e['failures']} / {e['runs']}", "n", e["rate"]),
        _td("issue", f"#{e['issue']}" if e["issue"] else ""),
        _id("test", _e(e["test"])),
    ]


HEADS = {
    "setup_cost": ["seconds", "fixture", "scope", "defined in", "runs", "tests", "rebuilt"],
    "slowest": ["seconds", "share", "set-up", "call", "teardown", "test"],
    "little_code": ["call", "lines run", "test"],
    "by_test_file": ["seconds", "share", "floor", "tests", "file"],
    "always_cost": ["seconds", "test"],
    "same_path": ["beyond fastest", "seconds", "own lines", "files", "tests"],
    "near_same": ["seconds", "common lines", "file", "tests (+ own lines beyond the common core)"],
    "startup_path": ["tests", "seconds", "median", "functions"],
    "by_file": ["coverage", "run / lines", "tests", "file"],
    "never_imported": ["lines", "kind", "file"],
    None: ["rate", "failed / runs", "issue", "test"],
}
ROWS = {
    "setup_cost": _row_setup_cost,
    "slowest": _row_slowest,
    "little_code": _row_little_code,
    "by_test_file": _row_by_test_file,
    "always_cost": _row_always_cost,
    "same_path": _row_same_path,
    "near_same": _row_near_same,
    "startup_path": _row_startup_path,
    "by_file": _row_by_file,
    "never_imported": _row_never_imported,
    None: _row_flaky,
}


def html(model: dict) -> str:
    from importlib.resources import files

    h = model["header"]
    sha = (h.get("sha") or "")[:10] or "unknown commit"
    body = [
        f"<h1>Suite health · map {_e(sha)}</h1>",
        f'<p class="meta">{_e((h.get("date") or "")[:10])} · {h["tests"]} tests · '
        f"{h['seconds']:.2f} s recorded · {h['maps']} map(s)</p>",
        '<ul class="head">',
    ]
    if h.get("head_differs"):
        body.append("<li>This checkout is not the map's commit: line numbers may have moved.</li>")
    if h["args"]:
        body.append(f"<li>Recorded with <code>{_e(' '.join(h['args']))}</code></li>")
    body.append(f"<li>Deselected when recorded: {h['deselected']}.</li>")
    body.append(f"<li>Tests that started a child process: {h['spawns']}.</li>")
    body += [
        f"<li>Skipped: <code>{_e(s['test'])}</code> ({_e(s['reason'])})</li>" for s in h["skipped"]
    ]
    body += [f"<li>Not Python: <code>{_e(f)}</code></li>" for f in h["unparsed"]]
    body += [f"<li>{_e(n)}</li>" for n in h["notes"]]
    body.append("</ul>")
    group_titles = {"time": "Time", "overlap": "Overlap", "coverage": "Coverage", "flaky": "Flaky"}
    shown = set()
    for group, key, title, points in SECTIONS:
        if group not in shown:
            body.append(f"<h2>{group_titles[group]}</h2>")
            shown.add(group)
        sec = model[group] if key is None else model[group].get(key, {})
        body.append(f"<h3>{_e(title)}</h3>")
        if sec.get("note"):
            body.append(f'<p class="note">{_e(sec["note"])}</p>')
            continue
        body.append(f'<p class="total">{_e(_total(key, sec))}</p>')
        body.append(f'<p class="points">points to: {_e(points)}</p>')
        if not sec["entries"]:
            continue
        head = "".join(f"<th>{_e(label)}</th>" for label in HEADS[key])
        rows = "".join("<tr>" + "".join(ROWS[key](e)) + "</tr>" for e in sec["entries"])
        body.append(
            f'<input class="filter" placeholder="filter" aria-label="filter {_e(title)}">'
            f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
        )
    page = (files("fastci") / "templates" / "report.html").read_text(encoding="utf-8")
    data = as_json(model).replace("</", "<\\/")
    return (
        page.replace("{{TITLE}}", _e(f"fastci report · {sha}"))
        .replace("{{MODEL}}", data)
        .replace("{{BODY}}", "\n".join(body))
    )
