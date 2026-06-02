from unittest.mock import patch

from audit.ai.agents import AgentDefinition, get_audit_agents
from audit.ai.output import PipelineReport
from audit.ai.pipeline import _make_submit_report_tool, report_to_markdown, run_pipeline
from audit.ai.report.typst_builder import _render_source, _sanitize_typst



def test_get_audit_agents_is_unique_and_nonempty() -> None:
    agents = get_audit_agents()
    assert len(agents) >= 1
    ids = [a.id for a in agents]
    assert len(ids) == len(set(ids))
    assert all(isinstance(a, AgentDefinition) for a in agents)
    assert all(a.prompt.strip() and a.name.strip() and a.description.strip() for a in agents)


class _FakeJob:
    pk = "job-xyz"
    repo_full_name = "acme/widgets"

    class _Path:
        name = "widgets"

        def __str__(self) -> str:
            return "/tmp/widgets"

    clone_path = _Path()


def test_run_pipeline_builds_report_from_orchestrator_holder() -> None:
    holder = {"risk_level": "medium", "summary": "Some risk.", "markdown": "## Findings\nstuff"}
    with patch("audit.ai.pipeline._run_orchestrator", return_value=holder):
        report = run_pipeline(_FakeJob())

    assert report.job_id == "job-xyz"
    assert report.repo_name == "acme/widgets"
    assert report.risk_level == "medium"
    assert report.summary == "Some risk."
    assert report.markdown == "## Findings\nstuff"
    assert report.completed_at  # ISO timestamp set


def test_sanitize_typst_escapes_dangerous_directives() -> None:
    src = '#import "secrets"\nnormal text\n#include "other"\n#eval("code")\n#sys.inputs\n'
    sanitized = _sanitize_typst(src)
    # dangerous directives replaced with escaped (non-executing) forms
    assert '\\#import "secrets"' in sanitized
    assert '\\#include "other"' in sanitized
    assert '\\#eval("code")' in sanitized
    assert '\\#sys.inputs' in sanitized
    assert "normal text" in sanitized  # safe content untouched
