"""Bridge ORM AuditSuite/AuditAgent rows to the pipeline's AgentDefinition contract."""

from audit.ai.agents import AgentDefinition


def suite_to_agent_definitions(suite) -> list[AgentDefinition]:
    return [
        AgentDefinition(
            id=a.agent_id, name=a.name, description=a.description, prompt=a.prompt
        )
        for a in suite.agents.filter(enabled=True)
    ]
