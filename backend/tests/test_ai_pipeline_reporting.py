from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from audit.ai.agents import AgentDefinition, get_audit_agents
from audit.ai.output import PipelineReport
from audit.ai.pipeline import (
    AgentOutputCapture,
    PipelineResult,
    _extract_agent_outputs,
    _make_submit_report_tool,
    report_to_markdown,
    run_pipeline,
)
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
    with patch("audit.ai.pipeline._run_orchestrator", return_value=(holder, [])):
        result = run_pipeline(_FakeJob(), agents=[])
    report = result.report
    assert report.job_id == "job-xyz"
    assert report.repo_name == "acme/widgets"
    assert report.risk_level == "medium"
    assert report.summary == "Some risk."
    assert report.markdown == "## Findings\nstuff"
    assert report.completed_at


def test_extract_agent_outputs_pairs_task_calls_to_tool_messages() -> None:
    messages = [
        HumanMessage(content="run"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"subagent_type": "project_overview", "description": "go"},
                    "id": "call_1",
                }
            ],
        ),
        ToolMessage(content="## Repo\nstuff", tool_call_id="call_1"),
        AIMessage(content="done"),
    ]
    outputs = _extract_agent_outputs(messages)
    assert outputs == [AgentOutputCapture(agent_id="project_overview", output="## Repo\nstuff")]


def test_extract_agent_outputs_handles_block_content() -> None:
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "task", "args": {"subagent_type": "a"}, "id": "c1"}
            ],
        ),
        ToolMessage(content=[{"type": "text", "text": "block out"}], tool_call_id="c1"),
    ]
    outputs = _extract_agent_outputs(messages)
    assert outputs == [AgentOutputCapture(agent_id="a", output="block out")]


def test_extract_agent_outputs_handles_multiple_parallel_calls() -> None:
    # The orchestrator fans out to all specialists in one turn — capture every one.
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "task", "args": {"subagent_type": "a"}, "id": "c1"},
                {"name": "task", "args": {"subagent_type": "b"}, "id": "c2"},
            ],
        ),
        ToolMessage(content="out A", tool_call_id="c1"),
        ToolMessage(content="out B", tool_call_id="c2"),
    ]
    outputs = _extract_agent_outputs(messages)
    assert outputs == [
        AgentOutputCapture(agent_id="a", output="out A"),
        AgentOutputCapture(agent_id="b", output="out B"),
    ]


def test_extract_agent_outputs_ignores_orphaned_task_call() -> None:
    # A task call with no matching ToolMessage (e.g. an aborted run) yields no capture.
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "task", "args": {"subagent_type": "a"}, "id": "c1"}],
        ),
    ]
    assert _extract_agent_outputs(messages) == []


def test_run_pipeline_returns_result_with_report_and_outputs() -> None:
    holder = {"risk_level": "low", "summary": "ok", "markdown": "## B\nx"}
    captures = [AgentOutputCapture(agent_id="project_overview", output="## Repo\ns")]
    with patch(
        "audit.ai.pipeline._run_orchestrator", return_value=(holder, captures)
    ):
        result = run_pipeline(_FakeJob(), agents=[])
    assert isinstance(result, PipelineResult)
    assert result.report.risk_level == "low"
    assert result.report.repo_name == "acme/widgets"
    assert result.agent_outputs == captures


def test_sanitize_typst_escapes_dangerous_directives() -> None:
    src = '#import "secrets"\nnormal text\n#include "other"\n#eval("code")\n#sys.inputs\n'
    sanitized = _sanitize_typst(src)
    # dangerous directives replaced with escaped (non-executing) forms
    assert '\\#import "secrets"' in sanitized
    assert '\\#include "other"' in sanitized
    assert '\\#eval("code")' in sanitized
    assert '\\#sys.inputs' in sanitized
    assert "normal text" in sanitized  # safe content untouched
