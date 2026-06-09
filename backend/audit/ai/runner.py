from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    SubAgent,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from audit.ai.model import get_llm
from audit.ai.output import PipelineReport, SubmittedReport
from audit.ai.prompts import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    ORCHESTRATOR_TASK_INSTRUCTIONS,
    SPECIALIST_SYSTEM_PROMPT_PREFIX,
)


@dataclass(frozen=True)
class AgentOutputCapture:
    agent_id: str
    output: str


@dataclass(frozen=True)
class PipelineResult:
    report: PipelineReport
    agent_outputs: list[AgentOutputCapture]


_ORCHESTRATOR_PROFILE = HarnessProfile(
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
)

register_harness_profile("anthropic", _ORCHESTRATOR_PROFILE)


class AgentOutputError(Exception):
    """Raised when the orchestrator fails to submit a report."""

    def __init__(self, agent_id: str, detail: str, cause: Exception) -> None:
        self.agent_id = agent_id
        self.detail = detail
        self.cause = cause
        super().__init__(
            f"Agent '{agent_id}' failed to produce a report: {detail} ({cause})"
        )


def _make_backend(repo_path: Path) -> CompositeBackend:
    # Virtual read-only access to repo; agent internals use ephemeral StateBackend
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/workspace/": FilesystemBackend(root_dir=str(repo_path), virtual_mode=True)
        },
    )


def _build_specialist_subagents(agents) -> list[SubAgent]:
    subagents: list[SubAgent] = []
    for agent in agents:
        subagents.append(
            {
                "name": agent.id,
                "description": agent.description,
                "system_prompt": (
                    f"{SPECIALIST_SYSTEM_PROMPT_PREFIX}\n\nFocus area:\n{agent.prompt}"
                ),
            }
        )
    return subagents


def _build_orchestrator_prompt(agents) -> str:
    subagent_list = "\n".join(f"- {a.id}: {a.description}" for a in agents)
    return f"{ORCHESTRATOR_TASK_INSTRUCTIONS}{subagent_list}"


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def _extract_agent_outputs(messages: list[BaseMessage]) -> list[AgentOutputCapture]:
    """Pair each `task` tool-call (→ subagent_type) with its ToolMessage (→ output)."""
    id_to_agent: dict[str, str] = {}
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for tc in m.tool_calls:
            if tc["name"] != "task":
                continue
            call_id = tc.get("id")
            subagent = tc.get("args", {}).get("subagent_type")
            if call_id and subagent:
                id_to_agent[call_id] = subagent

    outputs: list[AgentOutputCapture] = []
    for m in messages:
        if isinstance(m, ToolMessage) and (m.tool_call_id in id_to_agent):
            outputs.append(
                AgentOutputCapture(
                    agent_id=id_to_agent[m.tool_call_id],
                    output=_message_text(m.content),
                )
            )
    return outputs


def _run_orchestrator(
    repo_path: Path, agents, model_name: str, orchestrator_prompt: str | None = None
):
    model = get_llm(model_name)
    system_prompt = orchestrator_prompt or ORCHESTRATOR_SYSTEM_PROMPT
    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        backend=_make_backend(repo_path),
        subagents=_build_specialist_subagents(agents),
        response_format=SubmittedReport,
    )
    state = agent.invoke(
        {"messages": [{"role": "user", "content": _build_orchestrator_prompt(agents)}]}
    )

    submitted = state.get("structured_response") if isinstance(state, dict) else None
    if not isinstance(submitted, SubmittedReport):
        raise AgentOutputError(
            "orchestrator",
            "no structured report produced",
            ValueError("Orchestrator did not return a structured response"),
        )
    messages = state.get("messages", []) if isinstance(state, dict) else []
    return submitted, _extract_agent_outputs(messages)


def run_pipeline(
    job, agents, model_name: str, orchestrator_prompt: str | None = None
) -> PipelineResult:
    """Run the orchestrated audit over the cloned repo and return a PipelineResult."""
    repo_name = job.repo_full_name or Path(str(job.clone_path)).name
    submitted, agent_outputs = _run_orchestrator(
        job.clone_path, agents, model_name, orchestrator_prompt
    )
    report = PipelineReport(
        job_id=str(job.pk),
        completed_at=datetime.now(tz=timezone.utc).isoformat(),
        repo_name=repo_name,
        summary=submitted.summary,
        markdown=submitted.markdown,
    )
    return PipelineResult(report=report, agent_outputs=agent_outputs)


def report_to_markdown(report: PipelineReport) -> str:
    """Compose the titled markdown document. The agent supplies body content only;
    Python owns the title/header."""
    lines = [
        "# VibeAudit Report",
        f"Repository: {report.repo_name}",
        "",
        "## Executive Summary",
        report.summary.strip() or "No summary provided.",
        "",
        report.markdown.strip(),
    ]
    return "\n".join(lines).strip() + "\n"
