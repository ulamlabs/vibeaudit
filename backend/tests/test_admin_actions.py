from unittest.mock import patch

import pytest
from django.contrib.admin.sites import site

from audit.admin import AuditJobAdmin, AuditRunAdmin
from audit.models import AuditAgent, AuditJob, AuditRun, AuditSuite
from github_app.models import Installation


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=21, account_login="octocat", account_type="User"
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


def _run(installation, suite, report_state="", status=AuditRun.Status.COMPLETED):
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="o/r", email="a@b.c"
    )
    job.state = AuditJob.State.READY
    job.save(update_fields=["state"])
    run = AuditRun.objects.create(job=job, suite=suite, status=status)
    if report_state:
        AuditRun.objects.filter(pk=run.pk).update(report_state=report_state)
        run.refresh_from_db()
    return run


@pytest.mark.django_db
def test_markdown_editable_only_while_awaiting_approval(installation, suite):
    admin_obj = AuditRunAdmin(AuditRun, site)
    awaiting = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    assert "markdown" not in admin_obj.get_readonly_fields(None, awaiting)
    assert "markdown" in admin_obj.get_fields(None, awaiting)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [
        AuditRun.ReportState.APPROVED,
        AuditRun.ReportState.SENT,
        AuditRun.ReportState.REJECTED,
        "",
    ],
)
def test_markdown_locked_outside_approval(installation, suite, state):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, state)
    assert "markdown" in admin_obj.get_readonly_fields(None, run)


@pytest.mark.django_db
def test_report_state_is_always_readonly(installation, suite):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    assert "report_state" in admin_obj.get_readonly_fields(None, run)


@pytest.mark.django_db
def test_approve_report_transitions_and_enqueues(installation, suite, rf):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    request = rf.get("/")
    with (
        patch.object(AuditRunAdmin, "message_user"),
        patch.object(AuditRun, "enqueue_send") as enqueue,
    ):
        admin_obj.approve_report(request, run.pk)
    enqueue.assert_called_once()
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.APPROVED


@pytest.mark.django_db
def test_reject_report_transitions_and_sends_nothing(installation, suite, rf):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    with (
        patch.object(AuditRunAdmin, "message_user"),
        patch.object(AuditRun, "enqueue_send") as enqueue,
    ):
        admin_obj.reject_report(rf.get("/"), run.pk)
    enqueue.assert_not_called()
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.REJECTED


@pytest.mark.django_db
def test_approve_report_on_already_approved_run_reports_error(installation, suite, rf):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.APPROVED)
    with (
        patch.object(AuditRunAdmin, "message_user") as message,
        patch.object(AuditRun, "enqueue_send") as enqueue,
    ):
        admin_obj.approve_report(rf.get("/"), run.pk)
    enqueue.assert_not_called()
    assert "Cannot transition report" in message.call_args[0][1]


@pytest.mark.django_db
def test_resend_report_requeues_an_approved_report(installation, suite, rf):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.APPROVED)
    with (
        patch.object(AuditRunAdmin, "message_user"),
        patch.object(AuditRun, "enqueue_send") as enqueue,
    ):
        admin_obj.resend_report(rf.get("/"), run.pk)
    enqueue.assert_called_once()


@pytest.mark.django_db
def test_resend_report_refuses_a_sent_report(installation, suite, rf):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.SENT)
    with (
        patch.object(AuditRunAdmin, "message_user") as message,
        patch.object(AuditRun, "enqueue_send") as enqueue,
    ):
        admin_obj.resend_report(rf.get("/"), run.pk)
    enqueue.assert_not_called()
    assert "Cannot resend" in message.call_args[0][1]
