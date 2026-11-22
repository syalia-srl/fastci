"""fastci says when a newer release exists, loudest in GitHub Actions."""

import json
import time

import pytest

from fastci import version_check as vc


@pytest.fixture
def latest(monkeypatch, tmp_path):
    """Answer the release lookup without the network; count the lookups."""
    calls = []
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("FASTCI_NO_VERSION_CHECK", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    def set_to(tag):
        def fake(token, timeout):
            calls.append(tag)
            if isinstance(tag, Exception):
                raise tag
            return tag

        monkeypatch.setattr(vc, "_fetch_latest", fake)
        return calls

    return set_to


@pytest.mark.parametrize(
    "current,latest_tag,out",
    [
        ("0.3.0", "v0.3.1", True),
        ("0.3.1", "v0.3.1", False),
        ("0.4.0", "v0.3.1", False),
        ("0.10.0", "v0.9.0", False),
        ("0.0.0", "v0.3.1", False),
        ("0.3.0", "nightly", False),
    ],
)
def test_out_of_date_compares_numbers(current, latest_tag, out):
    assert vc.out_of_date(current, latest_tag) is out


def test_a_laptop_gets_one_line_and_the_answer_is_cached_a_day(latest, capsys):
    calls = latest("v9.0.0")
    vc.check("0.3.0", token=None)
    vc.check("0.3.0", token=None)
    err = capsys.readouterr().err
    assert err.count("fastci: fastci 0.3.0 is out of date; 9.0.0 is out") == 2
    assert "@v9.0.0" in err and "::warning" not in err
    assert calls == ["v9.0.0"]  # the second run read the cache


def test_an_old_cache_is_asked_again(latest, tmp_path):
    calls = latest("v9.0.0")
    path = tmp_path / "cache" / "fastci" / "latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"checked": time.time() - 2 * 86400, "tag": "v1.0.0"}))
    vc.check("0.3.0", token=None)
    assert calls == ["v9.0.0"]


def test_github_actions_gets_a_warning_annotation_and_a_summary_line(
    latest, monkeypatch, tmp_path, capsys
):
    latest("v9.0.0")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    vc.check("0.3.0", token="t")
    captured = capsys.readouterr()
    assert captured.out.startswith("::warning title=fastci is out of date::")
    assert "fastci 0.3.0 is out of date" in summary.read_text()


def test_no_network_and_the_opt_out_say_nothing(latest, monkeypatch, capsys):
    latest(OSError("offline"))
    vc.check("0.3.0", token=None)
    monkeypatch.setenv("FASTCI_NO_VERSION_CHECK", "1")
    latest("v9.0.0")
    vc.check("0.3.0", token=None)
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""
