import logging
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, User
from django.core import mail

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
