from app.html_utils import strip_html


def test_strip_html_removes_tags():
    assert strip_html("<p>Build <b>great</b> things.</p>") == "Build great things."


def test_strip_html_decodes_entities():
    # Regression: Workday/Greenhouse/Lever all return HTML with entities like
    # &#39; for apostrophes — left undecoded, "Red Hat&#39;s team" would leak
    # straight into the LLM prompt looking garbled.
    assert strip_html("Red Hat&#39;s team &amp; friends") == "Red Hat's team & friends"


def test_strip_html_collapses_whitespace():
    assert strip_html("<p>Line one</p>\n\n<p>Line   two</p>") == "Line one Line two"


def test_strip_html_handles_empty_input():
    assert strip_html("") == ""
    assert strip_html(None) == ""
