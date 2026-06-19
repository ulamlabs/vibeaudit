from bs4 import BeautifulSoup
from slugify import slugify

import nh3
from markdown_it import MarkdownIt


_md = MarkdownIt("commonmark").enable("table")


def render_markdown_safe(md: str) -> str:
    if not md:
        return ""
    return nh3.clean(_md.render(md))


def render_markdown_to_html(md: str) -> tuple[str, list[tuple[int, str, str]]]:
    if not md:
        return "", []
    html = nh3.clean(_md.render(md))
    # annotate headings with id="slug" so ToC links and WeasyPrint page targets work
    soup = BeautifulSoup(html, "html.parser")
    toc_items: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = tag.get_text()
        slug = slugify(text)
        # Deduplicate slugs by appending -2, -3, etc.
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 1
        tag["id"] = slug
        toc_items.append((int(tag.name[1]), text, slug))
    return str(soup), toc_items
