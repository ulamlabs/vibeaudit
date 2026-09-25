"""Regression tests for the admin's markdown render methods.

These call the display methods directly (no DB needed) to guard against the
`format_html` misuse that crashed on report bodies containing literal braces.
"""

import pytest
from django.contrib.admin.sites import site
from django.test import override_settings

from audit.admin import AgentRunOutputInline, AuditRunAdmin, AuditSuiteAdminForm
from audit.models import AgentRunOutput, AuditRun, AuditSuite


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


@pytest.mark.django_db
def test_suite_form_keeps_unlisted_current_model_selectable():
    suite = AuditSuite.objects.create(name="Old", model="claude-retired-1")
    with override_settings(AVAILABLE_AI_MODELS=["claude-sonnet-5"]):
        choices = AuditSuiteAdminForm(instance=suite).fields["model"].widget.choices
    assert [value for value, _ in choices] == ["claude-retired-1", "claude-sonnet-5"]


def test_suite_form_offers_only_listed_models_for_new_suite():
    with override_settings(AVAILABLE_AI_MODELS=["claude-sonnet-5"]):
        choices = AuditSuiteAdminForm().fields["model"].widget.choices
    assert [value for value, _ in choices] == ["claude-sonnet-5"]
