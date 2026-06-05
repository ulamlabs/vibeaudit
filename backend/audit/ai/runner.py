import logging
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
from django.conf import settings
from langchain_core.tools import tool

from audit.ai.model import get_llm
from audit.ai.output import PipelineReport
from audit.ai.prompts import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    ORCHESTRATOR_TASK_INSTRUCTIONS,
    SPECIALIST_SYSTEM_PROMPT_PREFIX,
)

logger = logging.getLogger(__name__)


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
    excluded_tools=frozenset({"write_todos"}),
)

_REGISTERED_PROFILES: set[str] = set()


def _ensure_profile_registered(model) -> None:
    """Register an orchestration profile with subagents enabled.

    Valid profile keys are 'provider' or 'provider:model'. For Ollama we use 'ollama'
    (provider-only) because the model name contains a colon (e.g. qwen2.5:7b) which
    would make the key ambiguous. For string models we use the full string as-is.
    """
    key = model if isinstance(model, str) else "ollama"
    if key not in _REGISTERED_PROFILES:
        register_harness_profile(key, _ORCHESTRATOR_PROFILE)
        _REGISTERED_PROFILES.add(key)


class AgentOutputError(Exception):
    """Raised when the orchestrator fails to submit a report."""

    def __init__(self, agent_id: str, detail: str, cause: Exception) -> None:
        self.agent_id = agent_id
        self.detail = detail
        self.cause = cause
        super().__init__(f"Agent '{agent_id}' failed to produce a report: {detail} ({cause})")


def _make_backend(repo_path: Path) -> CompositeBackend:
    # Virtual read-only access to repo; agent internals use ephemeral StateBackend
    return CompositeBackend(
        default=StateBackend(),
        routes={"/workspace/": FilesystemBackend(root_dir=str(repo_path), virtual_mode=True)},
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


def _make_submit_report_tool(submitted_report: dict):
    """Build a one-shot tool that records the orchestrator's final report into
    `submitted_report`."""

    @tool
    def submit_report(risk_level: str, summary: str, markdown: str) -> str:
        """Submit the final audit report. Call exactly once when synthesis is complete.

        Args:
            risk_level: overall risk — one of critical/high/medium/low/info.
            summary: one executive paragraph spanning all findings.
            markdown: the full report body as markdown (content only, no top-level title).
        """
        if submitted_report:
            logger.warning("submit_report called more than once; ignoring duplicate call")
            return "Report already submitted — ignoring duplicate call."
        submitted_report["risk_level"] = risk_level
        submitted_report["summary"] = summary
        submitted_report["markdown"] = markdown
        return "Report submitted."

    return submit_report


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _extract_agent_outputs(messages) -> list[AgentOutputCapture]:
    """Pair each `task` tool-call (→ subagent_type) with its ToolMessage (→ output)."""
    id_to_agent: dict[str, str] = {}
    for m in messages:
        # tool_calls are dicts at runtime (TypedDict); the getattr path is defensive only.
        for tc in getattr(m, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name != "task":
                continue
            args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            call_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            subagent = (args or {}).get("subagent_type")
            if call_id and subagent:
                id_to_agent[call_id] = subagent

    outputs: list[AgentOutputCapture] = []
    for m in messages:
        call_id = getattr(m, "tool_call_id", None)
        if call_id and call_id in id_to_agent:
            outputs.append(
                AgentOutputCapture(agent_id=id_to_agent[call_id], output=_message_text(m.content))
            )
    return outputs


def _run_orchestrator(repo_path: Path, agents, orchestrator_prompt: str | None = None):
    model = get_llm()
    _ensure_profile_registered(model)
    submitted_report: dict = {}
    system_prompt = orchestrator_prompt or ORCHESTRATOR_SYSTEM_PROMPT
    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        backend=_make_backend(repo_path),
        subagents=_build_specialist_subagents(agents),
        tools=[_make_submit_report_tool(submitted_report)],
    )
    state = agent.invoke(
        {"messages": [{"role": "user", "content": _build_orchestrator_prompt(agents)}]}
    )

    if "markdown" not in submitted_report:
        raise AgentOutputError(
            "orchestrator",
            "submit_report was never called",
            ValueError("No report submitted by orchestrator"),
        )
    messages = state.get("messages", []) if isinstance(state, dict) else []
    return submitted_report, _extract_agent_outputs(messages)


def run_pipeline(job, agents, orchestrator_prompt: str | None = None) -> PipelineResult:
    """Run the orchestrated audit over the cloned repo and return a PipelineResult."""
    repo_name = getattr(job, "repo_full_name", None) or Path(str(job.clone_path)).name
    submitted_report, agent_outputs = _run_orchestrator(
        job.clone_path, agents, orchestrator_prompt
    )
    report = PipelineReport(
        job_id=str(job.pk),
        completed_at=datetime.now(tz=timezone.utc).isoformat(),
        repo_name=repo_name,
        risk_level=submitted_report["risk_level"],
        summary=submitted_report["summary"],
        markdown=submitted_report["markdown"],
    )
    return PipelineResult(report=report, agent_outputs=agent_outputs)


def report_to_markdown(report: PipelineReport) -> str:
    """Compose the titled markdown document. The agent supplies body content only;
    Python owns the title/header."""
    lines = [
        "# VibeAudit Report",
        f"Repository: {report.repo_name}",
        f"Overall risk level: {report.risk_level}",
        "",
        "## Executive Summary",
        report.summary.strip() or "No summary provided.",
        "",
        report.markdown.strip(),
    ]
    return "\n".join(lines).strip() + "\n"
