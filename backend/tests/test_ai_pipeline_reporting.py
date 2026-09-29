from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from audit.ai.output import SubmittedReport
from audit.ai.runner import (
    AgentOutputCapture,
    PipelineResult,
    _extract_agent_outputs,
    run_pipeline,
)


class _FakeJob:
    pk = "job-xyz"
    repo_full_name = "acme/widgets"

    class _Path:
        name = "widgets"

        def __str__(self) -> str:
            return "/tmp/widgets"

    clone_path = _Path()


def test_extract_agent_outputs_handles_block_content() -> None:
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "task", "args": {"subagent_type": "a"}, "id": "c1"}],
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
    submitted = SubmittedReport(summary="Some risk.", markdown="## Findings\nstuff")
    captures = [AgentOutputCapture(agent_id="project_overview", output="## Repo\ns")]
    with patch("audit.ai.runner._run_orchestrator", return_value=(submitted, captures)):
        result = run_pipeline(_FakeJob(), agents=[], model_name="claude-sonnet-5")
    assert isinstance(result, PipelineResult)
    assert result.report.job_id == "job-xyz"
    assert result.report.repo_name == "acme/widgets"
    assert result.report.summary == "Some risk."
    assert result.report.markdown == "## Findings\nstuff"
    assert result.report.completed_at
    assert result.agent_outputs == captures
