from __future__ import annotations

import html
from pathlib import Path

from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.safestring import mark_safe
from audit.rendering import render_markdown_to_html


def _load_template_string(run) -> str | None:
    if run.suite.report_template:
        return run.suite.report_template
    template_path = settings.REPORT_TEMPLATE_PATH
    if template_path:
        path = Path(template_path)
        if path.exists():
            return path.read_text()
    return None


def _build_toc_html(toc_items: list[tuple[int, str, str]]) -> str:
    if not toc_items:
        return ""
    items_html = "\n".join(
        f'    <li class="toc-h{level}"><a href="#{html.escape(slug)}">{html.escape(text)}</a></li>'
        for level, text, slug in toc_items
    )
    return (
        '<nav class="toc">\n'
        '  <h2 class="toc-title">Contents</h2>\n'
        "  <ul>\n"
        f"{items_html}\n"
        "  </ul>\n"
        "</nav>"
    )


def _render_html(run) -> str:
    report_html, toc_items = render_markdown_to_html(run.markdown or "")
    toc_html = _build_toc_html(toc_items)
    context = {
        "run": run,
        "report_html": mark_safe(report_html),
        "toc": mark_safe(toc_html),
        "toc_items": toc_items,
        "generated_at": timezone.now(),
    }
    template_source = _load_template_string(run)
    if template_source is not None:
        return engines["django"].from_string(template_source).render(context)
    return render_to_string("report/report.html", context)


def render_pdf(run) -> bytes:
    from weasyprint import HTML  # lazy import — requires system pango/gobject libs

    return HTML(string=_render_html(run)).write_pdf()
