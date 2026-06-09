import nh3
from markdown_it import MarkdownIt


_md = MarkdownIt("commonmark").enable("table")


def render_markdown_safe(md: str) -> str:
    if not md:
        return ""
    return nh3.clean(_md.render(md))
