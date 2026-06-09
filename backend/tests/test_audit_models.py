from unittest.mock import patch

import pytest

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
    suite = AuditSuite.objects.create(name="Default", is_default=True)
    AuditAgent.objects.create(
        suite=suite,
        agent_id="project_overview",
        name="Project Overview",
        description="Summarize the repo.",
        prompt="Summarize /workspace/.",
    )
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
def test_approve_moves_to_ready_and_starts_run(installation, default_suite):
    job = _job(installation, AuditJob.State.AWAITING_APPROVAL)
    task = type("Task", (), {"id": "task-1"})()
    with patch(
        "audit.tasks.execute_audit_run.apply_async", return_value=task
    ) as apply_async:
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
def test_start_run_applies_suite_timeout_overrides(installation, default_suite):
    job = _job(installation, AuditJob.State.READY)
    default_suite.task_soft_time_limit_seconds = 123
    default_suite.task_time_limit_seconds = 456
    default_suite.save(
        update_fields=["task_soft_time_limit_seconds", "task_time_limit_seconds"]
    )

    task = type("Task", (), {"id": "task-2"})()
    with patch(
        "audit.tasks.execute_audit_run.apply_async", return_value=task
    ) as apply_async:
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
def test_suite_to_agent_definitions_only_enabled_in_order():
    suite = AuditSuite.objects.create(name="S")
    AuditAgent.objects.create(
        suite=suite, agent_id="b", name="B", description="d", prompt="p", position=1
    )
    AuditAgent.objects.create(
        suite=suite, agent_id="a", name="A", description="d", prompt="p", position=0
    )
    AuditAgent.objects.create(
        suite=suite,
        agent_id="off",
        name="Off",
        description="d",
        prompt="p",
        position=2,
        enabled=False,
    )
    defs = suite_to_agent_definitions(suite)
    assert [d.id for d in defs] == ["a", "b"]
