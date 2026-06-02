from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    """A specialist audit agent. In-code for now; future: load from an ORM model.

    `get_audit_agents()` is the single seam the future ORM swap replaces.
    """

    id: str
    name: str
    description: str  # delegation blurb the orchestrator sees when choosing subagents
    prompt: str       # specialist focus instructions


# Default in-code agent: a single lightweight specialist for local/token-constrained
# runs. The real multi-specialist prompts are managed out-of-band (ORM-backed suites
# are the planned replacement); `get_audit_agents()` is the single seam that swap
# replaces.
_DEFAULT_AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        id="project_overview",
        name="Project Overview",
        description="Summarize repository structure and file statistics.",
        prompt=(
            "List the top-level directories and file counts by extension in /workspace/. "
            "Return a brief markdown summary with two sections: "
            "## Repository Structure and ## File Statistics. "
            "Keep the response under 300 words."
        ),
    ),
]


def get_audit_agents() -> list[AgentDefinition]:
    """Return the active specialist agents.

    Single seam for a future ORM-backed implementation: swap this body to query the DB.
    """
    return _DEFAULT_AGENTS
