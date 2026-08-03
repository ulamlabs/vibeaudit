import logging
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, User
from django.core import mail
from django.utils import timezone

from audit.email import (
    send_report_approval_notification,
    send_report_email,
    send_run_failure_notification,
)
from audit.models import AuditAgent, AuditJob, AuditRun, AuditSuite
from github_app.models import Installation


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=11, account_login="octocat", account_type="User"
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


@pytest.fixture(autouse=True)
def html_alternatives_attached():
    """
    `audit.email._IS_CONSOLE_EMAIL` is captured at import from
    settings.EMAIL_BACKEND, so whether the HTML alternative is attached depends
    on whether the checkout has a .env with mail credentials. Pin it so these
    assertions hold in CI too.
    """
    with patch("audit.email._IS_CONSOLE_EMAIL", False):
        yield


@pytest.fixture
def notified_staff():
    group, _ = Group.objects.get_or_create(name="audit_notifications")
    user = User.objects.create_user(
        username="ops", email="ops@example.com", is_staff=True
    )
    user.groups.add(group)
    return user


def _completed_run(installation, suite, **kwargs):
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="octocat/hello", email="x@y.z"
    )
    job.state = AuditJob.State.READY
    job.save(update_fields=["state"])
    return AuditRun.objects.create(
        job=job, suite=suite, status=AuditRun.Status.COMPLETED, **kwargs
    )


@pytest.mark.django_db
def test_approval_notification_goes_to_staff_with_admin_link(
    installation, suite, notified_staff, settings
):
    settings.SITE_URL = "https://audit.example.com"
    run = _completed_run(installation, suite, summary="Found 3 issues")
    send_report_approval_notification(run)
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["ops@example.com"]
    assert "Awaiting Approval" in msg.subject
    html = msg.alternatives[0][0]
    assert f"/admin/audit/auditrun/{run.pk}/change/" in html
    assert "Found 3 issues" in html
    assert "octocat/hello" in html


@pytest.mark.django_db
def test_approval_notification_never_reaches_the_submitter(
    installation, suite, notified_staff
):
    run = _completed_run(installation, suite)
    send_report_approval_notification(run)
    assert mail.outbox[0].to == ["ops@example.com"]
    assert run.job.email not in mail.outbox[0].to


@pytest.mark.django_db
def test_run_failure_notification_goes_to_staff_with_reason(
    installation, suite, notified_staff
):
    run = _completed_run(installation, suite)
    AuditRun.objects.filter(pk=run.pk).update(
        status=AuditRun.Status.FAILED, error="worker exploded"
    )
    run.refresh_from_db()
    send_run_failure_notification(run)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ops@example.com"]
    assert "worker exploded" in mail.outbox[0].alternatives[0][0]


@pytest.mark.django_db
def test_notifications_are_best_effort(installation, suite, notified_staff, caplog):
    """A broken send must not propagate into the caller, and must not go silent."""
    run = _completed_run(installation, suite)
    caplog.set_level(logging.ERROR, logger="audit.email")
    with patch("audit.email._send", side_effect=Exception("smtp down")):
        send_report_approval_notification(run)
        send_run_failure_notification(run)
    assert mail.outbox == []
    # Swallowing the error without logging it would make failures invisible.
    assert caplog.text.count("Failed to send") == 2


@pytest.mark.django_db
def test_notifications_noop_without_recipients(installation, suite):
    run = _completed_run(installation, suite)
    send_report_approval_notification(run)
    assert mail.outbox == []


@pytest.mark.django_db
def test_send_report_email_propagates_failures(installation, suite):
    """The caller needs the error so it can leave report_state at 'approved'."""
    run = _completed_run(installation, suite, markdown="# R")
    with (
        patch("audit.email.render_pdf", return_value=b"%PDF-"),
        patch("audit.email._send", side_effect=Exception("smtp down")),
        pytest.raises(Exception, match="smtp down"),
    ):
        send_report_email(run)


@pytest.mark.django_db
def test_send_failure_email_is_gone():
    import audit.email

    assert not hasattr(audit.email, "send_failure_email")


from audit.tasks import send_approved_report


def _approved_run(installation, suite):
    run = _completed_run(installation, suite, markdown="# Report")
    AuditRun.objects.filter(pk=run.pk).update(
        report_state=AuditRun.ReportState.APPROVED
    )
    run.refresh_from_db()
    return run


@pytest.mark.django_db
def test_send_approved_report_sends_marks_sent_and_cleans_up(installation, suite):
    run = _approved_run(installation, suite)
    with (
        patch("audit.tasks.send_report_email") as send,
        patch("audit.models.AuditJob.maybe_cleanup_sources") as cleanup,
    ):
        send_approved_report(run.pk)
    send.assert_called_once()
    cleanup.assert_called_once_with(run.job_id)
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.SENT
    assert run.sending_since is None


@pytest.mark.django_db
def test_send_failure_leaves_report_approved(installation, suite):
    """A failed send must stay retryable via the admin's Resend action."""
    run = _approved_run(installation, suite)
    with (
        patch("audit.tasks.send_report_email", side_effect=Exception("smtp down")),
        patch("audit.models.AuditJob.maybe_cleanup_sources") as cleanup,
    ):
        send_approved_report(run.pk)
    cleanup.assert_not_called()
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.APPROVED
    assert run.sending_since is None


@pytest.mark.django_db
def test_send_approved_report_skips_runs_not_approved(installation, suite):
    """A redelivered message must not re-send an already-sent report."""
    run = _completed_run(installation, suite)
    AuditRun.objects.filter(pk=run.pk).update(report_state=AuditRun.ReportState.SENT)
    with patch("audit.tasks.send_report_email") as send:
        send_approved_report(run.pk)
    send.assert_not_called()


@pytest.mark.django_db
def test_send_approved_report_skips_a_run_already_claimed_sending(
    installation, suite
):
    """Simulates the second of two racing workers: the first already claimed
    the run (approved -> sending), so this call must not send a second time."""
    run = _approved_run(installation, suite)
    AuditRun.objects.filter(pk=run.pk).update(
        report_state=AuditRun.ReportState.SENDING, sending_since=timezone.now()
    )
    with patch("audit.tasks.send_report_email") as send:
        send_approved_report(run.pk)
    send.assert_not_called()
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.SENDING


@pytest.mark.django_db
def test_send_approved_report_claims_sending_before_send(installation, suite):
    """Successful delivery moves approved -> sending -> sent."""
    run = _approved_run(installation, suite)
    seen_state = {}

    def _capture(sent_run):
        seen_state["report_state"] = AuditRun.objects.get(pk=sent_run.pk).report_state

    with patch("audit.tasks.send_report_email", side_effect=_capture):
        send_approved_report(run.pk)
    assert seen_state["report_state"] == AuditRun.ReportState.SENDING
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.SENT


@pytest.mark.django_db
def test_send_approved_report_closes_the_job(installation, suite):
    """End to end: the last report sent releases the clone."""
    run = _approved_run(installation, suite)
    with patch("audit.tasks.send_report_email"):
        send_approved_report(run.pk)
    run.job.refresh_from_db()
    assert run.job.state == AuditJob.State.CLOSED


@pytest.mark.django_db
def test_send_approved_report_keeps_job_open_while_another_awaits(
    installation, suite
):
    run = _approved_run(installation, suite)
    other = AuditRun.objects.create(
        job=run.job, suite=suite, status=AuditRun.Status.COMPLETED
    )
    AuditRun.objects.filter(pk=other.pk).update(
        report_state=AuditRun.ReportState.AWAITING_APPROVAL
    )
    with patch("audit.tasks.send_report_email"):
        send_approved_report(run.pk)
    run.job.refresh_from_db()
    assert run.job.state == AuditJob.State.READY
