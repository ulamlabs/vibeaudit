"""Regression tests for the admin's markdown render methods.

These call the display methods directly (no DB needed) to guard against the
`format_html` misuse that crashed on report bodies containing literal braces.
"""

from django.contrib.admin.sites import site

from audit.admin import AgentRunOutputInline, AuditRunAdmin
from audit.models import AgentRunOutput, AuditRun


class _Obj:
    def __init__(self, markdown="", output=""):
        self.markdown = markdown
        self.output = output


def test_report_html_renders_markdown_with_braces_safely():
    admin_obj = AuditRunAdmin(AuditRun, site)
    html = admin_obj.report_html(_Obj(markdown="# Title\n\n```py\nd = {'k': 1}\n```"))
    assert 'class="md-preview"' in html  # styling hook for the scoped admin stylesheet
    assert "<h1>Title</h1>" in html
    assert "{'k': 1}" in html  # literal braces survive — no format-string crash


def test_report_html_empty_returns_dash():
    admin_obj = AuditRunAdmin(AuditRun, site)
    assert admin_obj.report_html(_Obj(markdown="")) == "—"


def test_rendered_output_sanitizes_and_renders():
    inline = AgentRunOutputInline(AgentRunOutput, site)
    html = inline.rendered_output(_Obj(output="**bold** <script>alert(1)</script>"))
    assert "<strong>bold</strong>" in html
    assert "<script>" not in html
