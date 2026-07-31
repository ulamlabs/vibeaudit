from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from audit.admin import AuditRunForm
from audit.ai.suites import suite_to_agent_definitions
from audit.models import AuditAgent, AuditJob, AuditRun, AuditSuite
from github_app.models import Installation


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=99, account_login="octocat", account_type="User"
    )


@pytest.fixture
def default_suite():
    suite = AuditSuite.objects.create(name="Default", is_default=True, model="m")
    agent = AuditAgent.objects.create(
        agent_id="test_agent",
        name="Test Agent",
        description="Summarize the repo.",
        prompt="Summarize /workspace/.",
    )
    suite.agents.add(agent)
    return suite


def _job(installation, state):
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="octocat/hello", email="x@y.z"
    )
    job.state = state
    job.save(update_fields=["state"])
    return job


@pytest.mark.django_db
def test_keep_sources_defaults_false(installation):
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="octocat/hello", email="x@y.z"
    )
    assert job.keep_sources is False


@pytest.mark.django_db
def test_valid_transition_pending_to_cloning(installation):
    job = _job(installation, AuditJob.State.PENDING)
    job.transition_to(AuditJob.State.CLONING)
    assert job.state == AuditJob.State.CLONING


@pytest.mark.django_db
def test_invalid_transition_raises(installation):
    job = _job(installation, AuditJob.State.PENDING)
    with pytest.raises(ValueError):
        job.transition_to(AuditJob.State.READY)


@pytest.mark.django_db
def test_approve_moves_to_ready_and_starts_run(
    installation, default_suite, django_capture_on_commit_callbacks
):
    job = _job(installation, AuditJob.State.AWAITING_APPROVAL)
    task = type("Task", (), {"id": "task-1"})()
    with (
        patch(
            "audit.tasks.execute_audit_run.apply_async", return_value=task
        ) as apply_async,
        # enqueue() publishes on transaction commit
        django_capture_on_commit_callbacks(execute=True),
    ):
        job.approve()
    job.refresh_from_db()
    assert job.state == AuditJob.State.READY
    run = AuditRun.objects.get(job=job)
    assert run.suite_id == default_suite.pk
    assert run.status == AuditRun.Status.PENDING
    run.refresh_from_db()
    assert run.celery_task_id == "task-1"
    apply_async.assert_called_once_with(
        args=[run.pk],
        soft_time_limit=600,
        time_limit=660,
    )


@pytest.mark.django_db
def test_start_run_requires_ready(installation, default_suite):
    job = _job(installation, AuditJob.State.AWAITING_APPROVAL)
    with pytest.raises(ValueError):
        job.start_run(default_suite)


@pytest.mark.django_db
def test_start_run_applies_suite_timeout_overrides(
    installation, default_suite, django_capture_on_commit_callbacks
):
    job = _job(installation, AuditJob.State.READY)
    default_suite.task_soft_time_limit_seconds = 123
    default_suite.task_time_limit_seconds = 456
    default_suite.save(
        update_fields=["task_soft_time_limit_seconds", "task_time_limit_seconds"]
    )

    task = type("Task", (), {"id": "task-2"})()
    with (
        patch(
            "audit.tasks.execute_audit_run.apply_async", return_value=task
        ) as apply_async,
        # enqueue() publishes on transaction commit
        django_capture_on_commit_callbacks(execute=True),
    ):
        run = job.start_run(default_suite)

    run.refresh_from_db()
    assert run.celery_task_id == "task-2"
    apply_async.assert_called_once_with(
        args=[run.pk],
        soft_time_limit=123,
        time_limit=456,
    )


@pytest.mark.django_db
def test_cleanup_deletes_clone_and_closes(installation):
    job = _job(installation, AuditJob.State.READY)
    with patch.object(AuditJob, "delete_clone") as del_clone:
        job.cleanup()
    job.refresh_from_db()
    assert job.state == AuditJob.State.CLOSED
    del_clone.assert_called_once()


@pytest.mark.django_db
def test_reject_moves_to_rejected(installation):
    job = _job(installation, AuditJob.State.AWAITING_APPROVAL)
    with patch.object(AuditJob, "delete_clone") as del_clone:
        job.reject()
    job.refresh_from_db()
    assert job.state == AuditJob.State.REJECTED
    del_clone.assert_called_once()


@pytest.mark.django_db
def test_approve_without_default_suite_does_not_strand(installation):
    # The seed migration creates a default suite; clear it so the no-default path runs.
    AuditSuite.objects.update(is_default=False)
    job = _job(installation, AuditJob.State.AWAITING_APPROVAL)
    with pytest.raises(ValueError):
        job.approve()
    job.refresh_from_db()
    assert (
        job.state == AuditJob.State.AWAITING_APPROVAL
    )  # still re-approvable, not stranded
    assert not AuditRun.objects.filter(job=job).exists()


@pytest.mark.django_db
def test_audit_run_form_rejects_run_for_non_ready_job(installation, default_suite):
    job = _job(installation, AuditJob.State.CLOSED)  # sources deleted
    form = AuditRunForm(data={"job": job.pk, "suite": default_suite.pk})
    assert not form.is_valid()
    assert "READY" in str(form.errors)


@pytest.mark.django_db
def test_audit_run_form_accepts_run_for_ready_job(installation, default_suite):
    job = _job(installation, AuditJob.State.READY)
    form = AuditRunForm(data={"job": job.pk, "suite": default_suite.pk})
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_suite_to_agent_definitions_returns_attached_agents():
    suite = AuditSuite.objects.create(name="S", model="m")
    agent_a = AuditAgent.objects.create(
        agent_id="a", name="A", description="d", prompt="p"
    )
    agent_b = AuditAgent.objects.create(
        agent_id="b", name="B", description="d", prompt="p"
    )
    AuditAgent.objects.create(agent_id="off", name="Off", description="d", prompt="p")
    suite.agents.add(agent_a, agent_b)
    defs = suite_to_agent_definitions(suite)
    ids = {d.id for d in defs}
    assert ids == {"a", "b"}
    assert "off" not in ids


@pytest.mark.django_db
def test_agent_can_be_shared_across_suites():
    agent = AuditAgent.objects.create(
        agent_id="shared_agent",
        name="Shared",
        description="A shared specialist.",
        prompt="Do the thing.",
    )
    suite_a = AuditSuite.objects.create(name="Suite A", model="m")
    suite_b = AuditSuite.objects.create(name="Suite B", model="m")
    suite_a.agents.add(agent)
    suite_b.agents.add(agent)
    assert suite_a.agents.count() == 1
    assert suite_b.agents.count() == 1
    # both suites point to the same single object — no duplication
    assert suite_a.agents.first().pk == suite_b.agents.first().pk


# ---------------------------------------------------------------------------
# AuditSuite.clean() — email template variable validation
# ---------------------------------------------------------------------------


def _suite(**kwargs):
    """Build an unsaved AuditSuite with required fields."""
    defaults = {"name": "Test", "model": "test-model"}
    defaults.update(kwargs)
    return AuditSuite(**defaults)


@pytest.mark.django_db
def test_email_template_blank_fields_valid():
    suite = _suite(email_html_body="")
    suite.full_clean()  # should not raise


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["email_html_body"])
def test_email_template_supported_variables_valid(field):
    template = (
        "{{ repo_name }} {{ summary }} {{ run_status }} "
        "{{ suite_name }} {{ pdf_attached }} {{ site_url }}"
    )
    suite = _suite(**{field: template})
    suite.full_clean()  # should not raise


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["email_html_body"])
def test_email_template_unknown_variable_raises(field):
    suite = _suite(**{field: "Hello {{ unknown_var }}"})
    with pytest.raises(ValidationError) as exc_info:
        suite.full_clean()
    assert field in exc_info.value.message_dict
    assert "unknown_var" in str(exc_info.value)


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["email_html_body"])
def test_email_template_invalid_syntax_raises(field):
    suite = _suite(**{field: "{% if %}"})  # missing condition
    with pytest.raises(ValidationError) as exc_info:
        suite.full_clean()
    assert field in exc_info.value.message_dict


@pytest.mark.django_db
def test_email_template_only_repo_name_valid():
    suite = _suite(email_html_body="Your report for {{ repo_name }} is ready.")
    suite.full_clean()  # should not raise


def _run(installation, default_suite, report_state=""):
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="o/r", email="a@b.c"
    )
    run = AuditRun.objects.create(job=job, suite=default_suite)
    if report_state:
        AuditRun.objects.filter(pk=run.pk).update(report_state=report_state)
        run.refresh_from_db(fields=["report_state"])
    return run


@pytest.mark.django_db
def test_report_state_defaults_blank(installation, default_suite):
    assert _run(installation, default_suite).report_state == ""


@pytest.mark.django_db
def test_report_awaiting_can_be_approved(installation, default_suite):
    run = _run(installation, default_suite, AuditRun.ReportState.AWAITING_APPROVAL)
    run.transition_report_to(AuditRun.ReportState.APPROVED)
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.APPROVED


@pytest.mark.django_db
def test_report_awaiting_can_be_rejected(installation, default_suite):
    run = _run(installation, default_suite, AuditRun.ReportState.AWAITING_APPROVAL)
    run.transition_report_to(AuditRun.ReportState.REJECTED)
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.REJECTED


@pytest.mark.django_db
def test_report_cannot_skip_approval(installation, default_suite):
    run = _run(installation, default_suite, AuditRun.ReportState.AWAITING_APPROVAL)
    with pytest.raises(ValueError, match="Cannot transition report"):
        run.transition_report_to(AuditRun.ReportState.SENT)


@pytest.mark.django_db
def test_rejected_report_is_terminal(installation, default_suite):
    run = _run(installation, default_suite, AuditRun.ReportState.REJECTED)
    with pytest.raises(ValueError, match="Cannot transition report"):
        run.transition_report_to(AuditRun.ReportState.APPROVED)


@pytest.mark.django_db
def test_report_transition_is_compare_and_set(installation, default_suite):
    """A double-clicked Approve must not let both requests through."""
    run = _run(installation, default_suite, AuditRun.ReportState.AWAITING_APPROVAL)
    stale = AuditRun.objects.get(pk=run.pk)  # second in-memory copy
    run.transition_report_to(AuditRun.ReportState.APPROVED)
    with pytest.raises(ValueError, match="concurrently"):
        stale.transition_report_to(AuditRun.ReportState.APPROVED)
