from pydantic import BaseModel, ConfigDict


class SubmittedReport(BaseModel):
    """Structured output the orchestrator must produce as its final response."""

    summary: str = ""
    markdown: str = ""


class PipelineReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    completed_at: str  # ISO timestamp
    repo_name: str
    summary: str = ""
    # TODO(security): `markdown` is free-form text synthesized partly from an UNTRUSTED
    # repository, which may contain prompt injection. Free-form markdown gives injected
    # content maximum latitude over the stored report and downstream renderers (typst PDF,
    # admin display). Future hardening: replace with a typed block schema (paragraph/code/
    # list/heading) that enforces an output grammar. Deferred — see design doc non-goals.
    markdown: str = ""
