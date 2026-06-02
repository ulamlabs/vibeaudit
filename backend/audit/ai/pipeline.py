import logging
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

from audit.ai.agents import get_audit_agents
from audit.ai.model import get_llm
from audit.ai.output import PipelineReport

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are an automated code auditor coordinating specialist subagents.
This is NOT an interactive session — no human will answer questions.

The repository is at /workspace/. Use subagents for deep analysis and synthesize one
coherent report. Do not ask follow-up questions.

When synthesis is complete you MUST call the `submit_report` tool exactly once. Do not
put the report in a normal message — only `submit_report` records it. Provide:
- risk_level: overall risk, one of critical/high/medium/low/info.
- summary: a single executive paragraph spanning all findings.
- markdown: the full report BODY as markdown (content sections only; do NOT include a
  top-level document title — that is added separately). No markdown code fences.

Only report what was observed in the repository or specialist outputs.
"""

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
    backend_type = getattr(settings, "AUDIT_SANDBOX_BACKEND", "local")
    if backend_type == "modal":
        raise NotImplementedError("Modal sandbox not yet configured")
    # Route /workspace/ to the real repo (read-only via virtual_mode); keep agent internals
    # (offloaded tool results, conversation history) in ephemeral StateBackend.
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
                    "You are a specialist technical auditor. "
                    "Analyze the repository at /workspace/ and return only concise markdown. "
                    "Do not return JSON. Do not use markdown code fences. "
                    "Use explicit headings requested in the task and keep findings evidence-based.\n\n"
                    f"Focus area:\n{agent.prompt}"
                ),
            }
        )
    return subagents


def _build_orchestrator_prompt(agents) -> str:
    subagent_list = "\n".join(f"- {a.id}: {a.description}" for a in agents)
    return (
        "Run exactly one task call for each specialist subagent listed below and gather "
        "their outputs.\n"
        "Then synthesize a single coherent report with no duplication, keeping the most "
        "concrete, evidence-backed version of each point.\n"
        "Finally, call submit_report exactly once.\n\n"
        "Specialist subagents:\n"
        f"{subagent_list}"
    )


def _make_submit_report_tool(holder: dict):
    """Build a one-shot tool that records the orchestrator's final report into `holder`."""

    @tool
    def submit_report(risk_level: str, summary: str, markdown: str) -> str:
        """Submit the final audit report. Call exactly once when synthesis is complete.

        Args:
            risk_level: overall risk — one of critical/high/medium/low/info.
            summary: one executive paragraph spanning all findings.
            markdown: the full report body as markdown (content only, no top-level title).
        """
        if holder:
            logger.warning("submit_report called more than once; ignoring duplicate call")
            return "Report already submitted — ignoring duplicate call."
        holder["risk_level"] = risk_level
        holder["summary"] = summary
        holder["markdown"] = markdown
        return "Report submitted."

    return submit_report


def _run_orchestrator(repo_path: Path) -> dict:
    model = get_llm()
    _ensure_profile_registered(model)
    holder: dict = {}
    agents = get_audit_agents()
    agent = create_deep_agent(
        model=model,
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        backend=_make_backend(repo_path),
        subagents=_build_specialist_subagents(agents),
        tools=[_make_submit_report_tool(holder)],
    )
    agent.invoke({"messages": [{"role": "user", "content": _build_orchestrator_prompt(agents)}]})

    if "markdown" not in holder:
        raise AgentOutputError(
            "orchestrator",
            "submit_report was never called",
            ValueError("No report submitted by orchestrator"),
        )
    return holder


def run_pipeline(job) -> PipelineReport:
    """Run the orchestrated audit over the cloned repo and return a PipelineReport."""
    repo_name = getattr(job, "repo_full_name", None) or Path(str(job.clone_path)).name
    holder = _run_orchestrator(job.clone_path)
    return PipelineReport(
        job_id=str(job.pk),
        completed_at=datetime.now(tz=timezone.utc).isoformat(),
        repo_name=repo_name,
        risk_level=holder["risk_level"],
        summary=holder["summary"],
        markdown=holder["markdown"],
    )


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
