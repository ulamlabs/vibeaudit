from audit.rendering import render_markdown_safe, render_markdown_to_html


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


def test_render_markdown_to_html_adds_heading_ids() -> None:
    html, items = render_markdown_to_html("## Hello World\n\ntext\n\n### A Finding\n\nmore")
    assert 'id="hello-world"' in html
    assert 'id="a-finding"' in html


def test_render_markdown_to_html_returns_toc_items() -> None:
    html, items = render_markdown_to_html("## Hello World\n\n### A Finding\n\ntext")
    assert items == [(2, "Hello World", "hello-world"), (3, "A Finding", "a-finding")]


def test_render_markdown_to_html_empty_returns_empty() -> None:
    html, items = render_markdown_to_html("")
    assert html == ""
    assert items == []


def test_render_markdown_to_html_still_strips_scripts() -> None:
    html, items = render_markdown_to_html("## Hi\n\n<script>alert(1)</script>")
    assert "<script>" not in html
    assert "alert(1)" not in html
