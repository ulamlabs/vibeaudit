from audit.rendering import render_markdown_safe


def test_renders_headings_and_emphasis() -> None:
    html = render_markdown_safe("# Title\n\nSome **bold** text.")
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html


def test_strips_script_tags() -> None:
    html = render_markdown_safe("# Hi\n\n<script>alert(1)</script>")
    assert "<h1>Hi</h1>" in html
    assert "<script>" not in html
    assert "alert(1)" not in html


def test_strips_event_handler_attributes() -> None:
    html = render_markdown_safe('<img src="x" onerror="alert(1)">')
    assert "onerror" not in html


def test_empty_input_returns_empty_string() -> None:
    assert render_markdown_safe("") == ""
