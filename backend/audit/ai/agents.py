from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    """A specialist audit agent definition used by the orchestrator pipeline."""

    id: str
    name: str
    description: str  # delegation blurb the orchestrator sees when choosing subagents
    prompt: str  # specialist focus instructions
