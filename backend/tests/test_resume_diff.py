from app.resume_diff import compute_diff, summarize_diff


def test_compute_diff_identical_text_is_all_equal():
    text = "line one\nline two\nline three"
    diff = compute_diff(text, text)
    assert all(d.type == "equal" for d in diff)
    assert [d.text for d in diff] == ["line one", "line two", "line three"]


def test_compute_diff_detects_added_line():
    base = "line one\nline two"
    tailored = "line one\nline two\nline three"
    diff = compute_diff(base, tailored)
    added = [d for d in diff if d.type == "added"]
    assert len(added) == 1
    assert added[0].text == "line three"


def test_compute_diff_detects_removed_line():
    base = "line one\nline two\nline three"
    tailored = "line one\nline three"
    diff = compute_diff(base, tailored)
    removed = [d for d in diff if d.type == "removed"]
    assert len(removed) == 1
    assert removed[0].text == "line two"


def test_compute_diff_detects_replaced_line_as_removed_plus_added():
    base = "Data Engineer with SQL experience"
    tailored = "Data Engineer with Python experience"
    diff = compute_diff(base, tailored)
    assert any(d.type == "removed" and "SQL" in d.text for d in diff)
    assert any(d.type == "added" and "Python" in d.text for d in diff)


def test_summarize_diff_no_changes():
    assert summarize_diff(compute_diff("same", "same")) == "No changes"


def test_summarize_diff_counts_added_and_removed():
    base = "a\nb"
    tailored = "a\nc\nd"
    summary = summarize_diff(compute_diff(base, tailored))
    assert "removed" in summary
    assert "added" in summary
