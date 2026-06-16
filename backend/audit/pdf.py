from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.safestring import mark_safe
from audit.rendering import render_markdown_safe


def _load_template_string(run) -> str | None:
    """Return template source string, or None to use the bundled default."""
    if run.suite.report_template:
        return run.suite.report_template
    template_path = settings.REPORT_TEMPLATE_PATH
    if template_path:
        path = Path(template_path)
        if path.exists():
            return path.read_text()
    return None


def render_pdf(run) -> bytes:
    """Render an AuditRun report as PDF bytes. Never writes to disk."""
    report_html = mark_safe(render_markdown_safe(run.markdown or ""))
    context = {
        "run": run,
        "report_html": report_html,
        "generated_at": timezone.now(),
    }
    template_source = _load_template_string(run)
    if template_source is not None:
        html_string = engines["django"].from_string(template_source).render(context)
    else:
        html_string = render_to_string("report/report.html", context)
    from weasyprint import HTML  # lazy import — requires system pango/gobject libs

    return HTML(string=html_string).write_pdf()
