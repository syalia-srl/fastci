"""Flaky-test issues on GitHub, against fakes of fetch._get and fetch._send."""

import io
import json

import pytest

from fastci import fetch, issues
from fastci.flaky import Entry


class Page(io.BytesIO):
    def __init__(self, items, next_url=None):
        super().__init__(json.dumps(items).encode())
        self.headers = {"Link": f'<{next_url}>; rel="next", <x>; rel="last"'} if next_url else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def item(number, id, state="open", title=None, **extra):
    body = f"intro\n\n{issues.marker(id)}\nmore" if id is not None else "no marker"
    return {
        "number": number,
        "state": state,
        "title": title or f"flaky: {id}",
        "body": body,
    } | extra


@pytest.fixture
def gh(monkeypatch):
    """Fake GitHub: ``pages`` maps a URL to (items, next URL); every _send is recorded."""
    pages, sent, got = {}, [], []

    def get(url, token, follow=True, timeout=60):
        got.append(url)
        items, nxt = pages[url]
        return Page(items, nxt)

    def send(method, url, token, body, timeout=30):
        sent.append((method, url, body))
        return {"number": 7}

    monkeypatch.setattr(fetch, "_get", get)
    monkeypatch.setattr(fetch, "_send", send)
    return pages, sent, got


FIRST = "https://api.github.com/repos/o/r/issues?labels=flaky&state=all&per_page=100"


def test_listed_follows_pages_and_skips_pull_requests(gh):
    pages, _, got = gh
    pages[FIRST] = ([item(1, "tests/a.py::x"), item(2, "tests/pr.py::y", pull_request={})], "p2")
    pages["p2"] = ([item(3, "tests/b.py::z", state="closed"), item(4, None)], None)

    found = issues.listed("o/r", "t", "flaky")

    assert got == [FIRST, "p2"]
    assert found == {
        "tests/a.py::x": issues.Issue(1, True),
        "tests/b.py::z": issues.Issue(3, False),
    }


def test_listed_gives_up_at_its_total_deadline(gh, monkeypatch):
    # Each read takes 2 seconds: the per-read timeout alone would let the pages go on.
    pages, _, got = gh
    pages[FIRST] = ([item(1, "tests/a.py::x")], "p2")
    pages["p2"], pages["p3"], pages["p4"] = ([], "p3"), ([], "p4"), ([], None)
    clock, timeouts = [0.0], []
    get = fetch._get

    def slow(url, token, follow=True, timeout=60):
        timeouts.append(timeout)
        clock[0] += 2
        return get(url, token, follow, timeout)

    monkeypatch.setattr(fetch, "_get", slow)
    monkeypatch.setattr(issues, "monotonic", lambda: clock[0])
    with pytest.raises(TimeoutError, match="gave up after 5 seconds"):
        issues.listed("o/r", "t", "flaky", timeout=5, total=5)
    assert got == [FIRST, "p2", "p3"] and timeouts == [5, 3, 1]


def test_listed_keys_by_the_marker_exactly_not_the_title(gh):
    pages, _, _ = gh
    long = "tests/t.py::x[a b]"
    pages[FIRST] = (
        [
            item(5, "tests/t.py::x[a", title="flaky: tests/t.py::x[a"),
            item(6, long, title="flaky: tests/t.py::x[a"),
        ],
        None,
    )
    assert issues.listed("o/r", "t", "flaky") == {
        "tests/t.py::x[a": issues.Issue(5, True),
        long: issues.Issue(6, True),
    }


def test_listed_ignores_a_marker_inside_a_line(gh):
    pages, _, _ = gh
    pages[FIRST] = ([{"number": 1, "state": "open", "body": "see <!-- fastci:test=a -->"}], None)
    assert issues.listed("o/r", "t", "flaky") == {}


def test_listed_reads_a_marker_on_a_crlf_line_and_a_null_body(gh):
    pages, _, _ = gh
    crlf = {"number": 1, "state": "open", "body": f"x\r\n{issues.marker('a::b')}\r\ny"}
    pages[FIRST] = ([crlf, {"number": 2, "state": "open", "body": None}], None)
    assert issues.listed("o/r", "t", "flaky") == {"a::b": issues.Issue(1, True)}


def test_listed_prefers_the_open_issue_of_two_with_one_marker(gh):
    pages, _, _ = gh
    pages[FIRST] = (
        [item(9, "a::b", state="closed"), item(8, "a::b"), item(2, "a::b", "closed")],
        None,
    )
    assert issues.listed("o/r", "t", "flaky") == {"a::b": issues.Issue(8, True)}


@pytest.mark.parametrize(
    "id",
    [
        "tests/t.py::test_x[svc-api]",
        "tests/t.py::test_y[a b/c]",
        "tests/t.py::test_z[ñandú-日本]",
        "tests/t.py::test_w[a-->b]",
        "tests/t.py::test_v[100%3E-->%25]",
        "tests/t.py::test_u[--!>]",
    ],
)
def test_node_ids_round_trip_through_the_marker(gh, id):
    m = issues.marker(id)
    assert m.startswith("<!-- fastci:test=") and m.endswith(" -->")
    assert "-->" not in m[len("<!-- ") : -len(" -->")]
    assert ">" not in m[len("<!-- ") : -len(" -->")]
    pages, _, _ = gh
    pages[FIRST] = ([{"number": 1, "state": "open", "body": issues.body(id, Entry(), 20)}], None)
    assert issues.listed("o/r", "t", "flaky") == {id: issues.Issue(1, True)}


def test_the_marker_of_a_plain_id_is_the_spec_s(gh):
    assert issues.marker("tests/t.py::x[a b]") == "<!-- fastci:test=tests/t.py::x[a b] -->"


def test_open_issue_posts_the_title_the_label_and_the_body(gh):
    _, sent, _ = gh
    assert issues.open_issue("o/r", "t", "flaky", "tests/t.py::x[a b]", "B") == 7
    assert sent == [
        (
            "POST",
            "https://api.github.com/repos/o/r/issues",
            {"title": "flaky: tests/t.py::x[a b]", "body": "B", "labels": ["flaky"]},
        )
    ]


def test_open_issue_cuts_a_title_longer_than_github_takes(gh):
    _, sent, _ = gh
    issues.open_issue("o/r", "t", "flaky", "tests/t.py::x[" + "a" * 300 + "]", "B")
    assert len(sent[0][2]["title"]) == 256


def test_edit_comment_and_set_open(gh):
    _, sent, _ = gh
    issues.edit("o/r", "t", 3, "new body")
    issues.comment("o/r", "t", 3, "hello")
    issues.set_open("o/r", "t", 3, False)
    issues.set_open("o/r", "t", 3, True)
    u = "https://api.github.com/repos/o/r/issues/3"
    assert sent == [
        ("PATCH", u, {"body": "new body"}),
        ("POST", u + "/comments", {"body": "hello"}),
        ("PATCH", u, {"state": "closed"}),
        ("PATCH", u, {"state": "open"}),
    ]


def entry():
    first = {"sha": "a1b2c3d4e5f6", "run": 4412, "date": "2026-11-03", "error": "E1"}
    last = {
        "sha": "e4f5a6b7c8d9",
        "run": 4519,
        "date": "2026-11-19",
        "error": 'AssertionError: timed out after 5.0 s waiting for "^C"\nsecond line',
    }
    return Entry(main=[0] * 47 + [1, 1, 1], first=first, last_failure=last)


def test_body_has_the_marker_the_rate_and_the_unflag_line():
    b = issues.body("tests/t.py::x[a b]", entry(), 20)
    lines = b.splitlines()
    assert "<!-- fastci:test=tests/t.py::x[a b] -->" in lines
    assert "| main, last 50 | 50 | 3 | 6% |" in lines
    assert "passed in a fresh process: it may depend on test order" in b
    assert "First seen: a1b2c3d (run 4412), 2026-11-03" in lines
    assert "Last failure: e4f5a6b (run 4519), 2026-11-19" in lines
    assert '  AssertionError: timed out after 5.0 s waiting for "^C"' in lines
    assert "  second line" in lines
    assert "main has run it 20 times in a row without a failure." in b


def test_body_without_history_or_unflag_after():
    b = issues.body("a::b", Entry(), 0)
    assert "| main | 0 | 0 | - |" in b.splitlines()
    assert "First seen" not in b and "Last failure" not in b
    assert "times in a row" not in b
    assert "It leaves the list when its issue is closed." in b


def test_send_posts_json_with_the_token(monkeypatch):
    seen = {}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        seen.update(
            method=req.get_method(),
            url=req.full_url,
            body=json.loads(req.data),
            auth=req.get_header("Authorization"),
            ctype=req.get_header("Content-type"),
            timeout=timeout,
        )
        return R(b'{"number": 4}')

    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)
    assert fetch._send("PATCH", "https://x/y", "tok", {"a": "ñ"}) == {"number": 4}
    assert seen == {
        "method": "PATCH",
        "url": "https://x/y",
        "body": {"a": "ñ"},
        "auth": "Bearer tok",
        "ctype": "application/json",
        "timeout": 30,
    }
