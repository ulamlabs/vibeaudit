from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings
from langgraph.errors import GraphRecursionError

from audit.ai.budget import CostBudgetExceeded
from audit.ai.output import PipelineReport
from audit.ai.runner import AgentOutputCapture, PipelineResult
from audit.models import AgentRunOutput, AuditAgent, AuditJob, AuditRun, AuditSuite
from audit.tasks import execute_audit_run
from github_app.models import Installation


@pytest.fixture(autouse=True)
def stub_email_sending():
    """Email sending is a side-effect tested separately; stub it out here."""
    with (
        patch("audit.tasks.send_report_approval_notification"),
        patch("audit.tasks.send_run_failure_notification"),
    ):
        yield


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=7, account_login="octocat", account_type="User"
    )


@pytest.fixture
def suite():
    s = AuditSuite.objects.create(name="S", is_default=True, model="claude-sonnet-4-6")
    agent, _ = AuditAgent.objects.get_or_create(
        agent_id="project_overview",
        defaults={"name": "PO", "description": "d", "prompt": "p"},
    )
    s.agents.add(agent)
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
        job_id="1",
        completed_at="t",
        repo_name="octocat/hello",
        summary="sum",
        markdown="## Body\nx",
    )
    return PipelineResult(
        report=report,
        agent_outputs=[
            AgentOutputCapture(agent_id="project_overview", output="## Repo\ns")
        ],
    )


@pytest.mark.django_db
def test_execute_audit_run_persists_results_and_outputs(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch("audit.ai.runner.run_pipeline", return_value=_result()):
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
def test_execute_audit_run_records_measured_cost(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)

    def _fake_pipeline(*args, cost_callback=None, **kwargs):
        # Simulate the pipeline driving instrumented model calls.
        cost_callback.total = 1.2345
        cost_callback.tracked = True
        return _result()

    with patch("audit.ai.runner.run_pipeline", side_effect=_fake_pipeline):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.status == AuditRun.Status.COMPLETED
    assert run.cost_usd == Decimal("1.2345")


@pytest.mark.django_db
def test_execute_audit_run_records_partial_cost_on_failure(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)

    def _fail_after_spend(*args, cost_callback=None, **kwargs):
        cost_callback.total = 0.5
        cost_callback.tracked = True
        raise RuntimeError("boom")

    with patch("audit.ai.runner.run_pipeline", side_effect=_fail_after_spend):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.status == AuditRun.Status.FAILED
    assert run.cost_usd == Decimal("0.5000")


@pytest.mark.django_db
def test_execute_audit_run_leaves_cost_null_when_untracked(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    # run_pipeline mock never touches the callback => tracked stays False.
    with patch("audit.ai.runner.run_pipeline", return_value=_result()):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.cost_usd is None


@pytest.mark.django_db
def test_execute_audit_run_marks_failed_on_error(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch("audit.ai.runner.run_pipeline", side_effect=RuntimeError("boom")):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.status == AuditRun.Status.FAILED
    assert "boom" in run.error


@pytest.mark.django_db
def test_execute_audit_run_reports_cost_budget_guard(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch(
        "audit.ai.runner.run_pipeline", side_effect=CostBudgetExceeded(30.0, 25.0)
    ):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.status == AuditRun.Status.FAILED
    assert "budget" in run.error and "$25.00" in run.error


@pytest.mark.django_db
def test_execute_audit_run_reports_recursion_guard(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch("audit.ai.runner.run_pipeline", side_effect=GraphRecursionError("loop")):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.status == AuditRun.Status.FAILED
    assert "recursion limit" in run.error


@pytest.mark.django_db
@pytest.mark.parametrize("keep_sources", [True, False])
def test_execute_audit_run_never_cleans_up_sources(installation, suite, keep_sources):
    """Cleanup moved to report approval — the clone must survive the run itself."""
    job = _ready_job(installation, keep_sources=keep_sources)
    run = AuditRun.objects.create(job=job, suite=suite)
    with (
        patch("audit.ai.runner.run_pipeline", return_value=_result()),
        patch.object(AuditJob, "cleanup") as cleanup,
    ):
        execute_audit_run(run.pk)
    cleanup.assert_not_called()
    job.refresh_from_db()
    assert job.state == AuditJob.State.READY


@pytest.mark.django_db
def test_completed_run_parks_report_awaiting_approval(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with patch("audit.ai.runner.run_pipeline", return_value=_result()):
        execute_audit_run(run.pk)
    run.refresh_from_db()
    assert run.status == AuditRun.Status.COMPLETED
    assert run.report_state == AuditRun.ReportState.AWAITING_APPROVAL


@pytest.mark.django_db
def test_completed_run_notifies_staff_not_submitter(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with (
        patch("audit.ai.runner.run_pipeline", return_value=_result()),
        patch("audit.tasks.send_report_approval_notification") as notify,
        patch("audit.email.send_report_email") as send_report,
    ):
        execute_audit_run(run.pk)
    notify.assert_called_once()
    send_report.assert_not_called()


@pytest.mark.django_db
def test_failed_run_notifies_staff_and_leaves_report_state_blank(installation, suite):
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with (
        patch("audit.ai.runner.run_pipeline", side_effect=RuntimeError("boom")),
        patch("audit.tasks.send_run_failure_notification") as notify,
    ):
        execute_audit_run(run.pk)
    notify.assert_called_once()
    run.refresh_from_db()
    assert run.status == AuditRun.Status.FAILED
    assert run.report_state == ""


@pytest.mark.django_db
def test_execute_audit_run_fails_when_model_not_in_whitelist(installation):
    suite = AuditSuite.objects.create(name="Bad", model="claude-unknown-99")
    job = _ready_job(installation)
    run = AuditRun.objects.create(job=job, suite=suite)
    with (
        patch("audit.ai.runner.run_pipeline") as mock_pipeline,
        override_settings(AVAILABLE_AI_MODELS=["claude-sonnet-4-6"]),
    ):
        execute_audit_run(run.pk)
    mock_pipeline.assert_not_called()
    run.refresh_from_db()
    assert run.status == AuditRun.Status.FAILED
    assert "claude-unknown-99" in run.error
    assert "AVAILABLE_AI_MODELS" in run.error
