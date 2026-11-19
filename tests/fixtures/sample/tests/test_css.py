from pathlib import Path


def test_css_is_red():
    assert "red" in Path("static/site.css").read_text()
