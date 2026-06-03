from unittest.mock import patch

import pytest

from audit.ai.output import PipelineReport
from audit.ai.pipeline import AgentOutputCapture, PipelineResult
from audit.models import AgentRunOutput, AuditAgent, AuditJob, AuditRun, AuditSuite
from audit.tasks import execute_audit_run
from github_app.models import Installation


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=7, account_login="octocat", account_type="User"
    )


@pytest.fixture
def suite():
    s = AuditSuite.objects.create(name="S", is_default=True)
    AuditAgent.objects.create(
        suite=s, agent_id="project_overview", name="PO", description="d", prompt="p"
    )
    return s


def _ready_job(installation, keep_sources=True):
    job = AuditJob.objects.create(
        installation=installation,
        repo_full_name="octocat/hello",
        email="x@y.z",
        keep_sources=keep_sources,
    )
    job.state = AuditJob.State.READY
    job.save(update_fields=["state"])
    return job


def _result():
    report = PipelineReport(
        job_id="1", completed_at="t", repo_name="octocat/hello",
        risk_level="high", summary="sum", markdown="## Body\nx",
    )
    return PipelineResult(
        report=report,
        agent_outputs=[AgentOutputCapture(agent_id="project_overview", output="## Repo\ns")],
    )


@pytest.mark.django_db
def test_execute_audit_run_persists_results_and_outputs(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch("audit.tasks.run_pipeline", return_value=_result()):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.status == AuditRun.Status.COMPLETED
    assert run.summary == "sum"
    assert run.markdown == "## Body\nx"
    assert run.started_at and run.finished_at
    outputs = list(AgentRunOutput.objects.filter(run=run))
    assert len(outputs) == 1
    assert outputs[0].agent_id == "project_overview"


@pytest.mark.django_db
def test_execute_audit_run_marks_failed_on_error(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch("audit.tasks.run_pipeline", side_effect=RuntimeError("boom")):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.status == AuditRun.Status.FAILED
    assert "boom" in run.error


@pytest.mark.django_db
def test_execute_audit_run_keeps_sources_by_default(installation, suite):
    job = _ready_job(installation, keep_sources=True)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch("audit.tasks.run_pipeline", return_value=_result()), \
         patch.object(AuditJob, "cleanup") as cleanup:
        execute_audit_run(run.pk)
    cleanup.assert_not_called()


@pytest.mark.django_db
def test_execute_audit_run_cleans_up_when_not_keeping_sources(installation, suite):
    job = _ready_job(installation, keep_sources=False)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch("audit.tasks.run_pipeline", return_value=_result()), \
         patch.object(AuditJob, "cleanup") as cleanup:
        execute_audit_run(run.pk)
    cleanup.assert_called_once()
