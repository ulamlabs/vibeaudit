"""Regression tests for the admin's markdown render methods.

These call the display methods directly (no DB needed) to guard against the
`format_html` misuse that crashed on report bodies containing literal braces.
"""

from unittest.mock import MagicMock

from django.contrib.admin.sites import site

from audit.admin import AgentRunOutputInline, AuditRunAdmin, AuditSuiteAdmin
from audit.models import AgentRunOutput, AuditAgent, AuditRun, AuditSuite


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


def _agent_form(*, deleted=False, empty=False, pk=None, position=0):
    form = MagicMock()
    form.cleaned_data = {} if empty else {"DELETE": deleted}
    agent = AuditAgent(position=position)
    agent.pk = pk
    form.instance = agent
    return form


def test_save_formset_numbers_new_agents_by_row_order():
    admin_obj = AuditSuiteAdmin(AuditSuite, site)
    forms = [
        _agent_form(),  # -> 0
        _agent_form(),  # -> 1
        _agent_form(deleted=True),  # skipped (marked for deletion)
        _agent_form(empty=True),  # skipped (blank extra row)
        _agent_form(),  # -> 2
    ]
    formset = MagicMock()
    formset.model = AuditAgent
    formset.forms = forms

    admin_obj.save_formset(request=None, form=None, formset=formset, change=False)

    assert forms[0].instance.position == 0
    assert forms[1].instance.position == 1
    assert forms[4].instance.position == 2
    formset.save.assert_called_once()  # base save_formset still persists the rows


def test_save_formset_keeps_existing_positions_and_appends_new():
    # A saved row keeps its (drag-set) position; new rows append after the max.
    admin_obj = AuditSuiteAdmin(AuditSuite, site)
    existing = _agent_form(pk=10, position=5)
    new_first = _agent_form()
    new_second = _agent_form()
    formset = MagicMock()
    formset.model = AuditAgent
    formset.forms = [existing, new_first, new_second]

    admin_obj.save_formset(request=None, form=None, formset=formset, change=True)

    assert existing.instance.position == 5
    assert new_first.instance.position == 6
    assert new_second.instance.position == 7
