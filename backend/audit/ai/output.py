from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

SeverityLevel = Literal["critical", "high", "medium", "low", "info"]
_VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}  # must mirror SeverityLevel


# Shared severity coercion used by PipelineReport.coerce_risk_level.
def _coerce_severity(v: object) -> str:
    if isinstance(v, str) and v in _VALID_SEVERITIES:
        return v
    return "info"


class PipelineReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    completed_at: str  # ISO timestamp
    repo_name: str
    risk_level: SeverityLevel = "info"
    summary: str = ""
    # TODO(security): `markdown` is free-form text synthesized partly from an UNTRUSTED
    # repository, which may contain prompt injection. Free-form markdown gives injected
    # content maximum latitude over the stored report and downstream renderers (typst PDF,
    # admin display). Future hardening: replace with a typed block schema (paragraph/code/
    # list/heading) that enforces an output grammar. Deferred — see design doc non-goals.
    markdown: str = ""

    @field_validator("risk_level", mode="before")
    @classmethod
    def coerce_risk_level(cls, v: object) -> str:
        return _coerce_severity(v)
