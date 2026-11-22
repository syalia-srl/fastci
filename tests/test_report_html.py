"""The report as one HTML page."""

import html
import re

from report_helpers import model_of, model_with_test

from fastci.report import render


def test_the_page_is_self_contained_and_phone_ready(health_map):
    page = render.html(model_of(health_map))
    assert not re.search(r'(src|href)="https?://', page)
    assert "@media (max-width:640px)" in page and "<style>" in page


def test_the_page_names_every_test_the_json_names(health_map):
    model = model_of(health_map)
    page = render.html(model)
    for test in re.findall(r"tests/[\w/]+\.py::[\w\[\]-]+", render.as_json(model)):
        assert html.escape(test) in page


def test_a_node_id_with_markup_cannot_break_the_page():
    page = render.html(model_with_test("t.py::test_x[</script><b>]"))
    embedded = page.split('id="model"')[1].split("</script>")[0]
    assert "</script><b>" not in embedded and "<b>]" not in page.replace("&lt;b&gt;", "")


def test_a_version_2_map_renders_as_text_and_html():
    from report_helpers import synthetic_suite

    from fastci.report import Suite, build

    s = synthetic_suite(durations_per_map=[[2.0, 1.0]])
    s.tm.phases, s.tm.version = [], 2
    model = build(Suite(maps=s.maps, root=s.root, reach_share=0.2), None)
    assert "t.py::test_0" in render.text(model, 10)
    assert "t.py::test_0" in render.html(model)
