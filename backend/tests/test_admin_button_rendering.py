"""Integration tests proving Unfold actually wires has_*_permission hooks to
the rendered change page — not just that the hooks return correct booleans in
isolation (see test_admin_actions.py for that). These go through the real
Django test client: URL registration -> Unfold's action rendering -> the hook.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

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


@pytest.fixture
def admin_client_logged_in(db):
    client_user = User.objects.create_superuser(
        username="root", email="root@example.com", password="pw"
    )
    from django.test import Client

    client = Client()
    client.force_login(client_user)
    return client


def _job(installation, state):
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="o/r", email="a@b.c"
    )
    AuditJob.objects.filter(pk=job.pk).update(state=state)
    job.refresh_from_db()
    return job


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


def _job_change_html(client, job):
    url = reverse("admin:audit_auditjob_change", args=[job.pk])
    response = client.get(url)
    assert response.status_code == 200, (
        f"unexpected status {response.status_code} for job change page"
    )
    return response.content.decode()


# AuditRunAdmin.Media references a real static asset (audit/md_preview.css).
# Under the whitenoise ManifestStaticFilesStorage used in production settings,
# rendering that <link> requires a collected staticfiles manifest, which the
# test run doesn't produce. Swap in the plain (non-manifest) storage just for
# rendering these pages — irrelevant to what we're verifying here, which is
# action-button wiring, not asset hashing.
_NON_MANIFEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _run_change_html(client, run):
    url = reverse("admin:audit_auditrun_change", args=[run.pk])
    with override_settings(STORAGES=_NON_MANIFEST_STORAGES):
        response = client.get(url)
    assert response.status_code == 200, (
        f"unexpected status {response.status_code} for run change page"
    )
    return response.content.decode()


# -- AuditJob -----------------------------------------------------------


@pytest.mark.django_db
def test_job_awaiting_approval_shows_approve_reject_cleanup_not_mark_failed(
    admin_client_logged_in, installation
):
    job = _job(installation, AuditJob.State.AWAITING_APPROVAL)
    html = _job_change_html(admin_client_logged_in, job)
    assert "/approve/" in html
    assert "/reject/" in html
    assert "/cleanup/" in html
    assert "/mark-failed/" not in html


@pytest.mark.django_db
def test_job_cloning_shows_only_mark_failed(admin_client_logged_in, installation):
    job = _job(installation, AuditJob.State.CLONING)
    html = _job_change_html(admin_client_logged_in, job)
    assert "/mark-failed/" in html
    assert "/approve/" not in html
    assert "/reject/" not in html
    assert "/cleanup/" not in html


@pytest.mark.django_db
def test_job_closed_shows_none_of_the_four_actions(admin_client_logged_in, installation):
    job = _job(installation, AuditJob.State.CLOSED)
    html = _job_change_html(admin_client_logged_in, job)
    assert "/approve/" not in html
    assert "/reject/" not in html
    assert "/cleanup/" not in html
    assert "/mark-failed/" not in html


# -- AuditRun -------------------------------------------------------------


@pytest.mark.django_db
def test_run_awaiting_approval_shows_approve_and_reject_not_resend_or_reset(
    admin_client_logged_in, installation, suite
):
    run = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    html = _run_change_html(admin_client_logged_in, run)
    assert "/approve-report/" in html
    assert "/reject-report/" in html
    assert "/resend-report/" not in html
    assert "/reset-send/" not in html
    assert "/download-pdf/" in html


@pytest.mark.django_db
def test_run_approved_shows_resend_not_approve_reject_or_reset(
    admin_client_logged_in, installation, suite
):
    run = _run(installation, suite, AuditRun.ReportState.APPROVED)
    html = _run_change_html(admin_client_logged_in, run)
    assert "/resend-report/" in html
    assert "/approve-report/" not in html
    assert "/reject-report/" not in html
    assert "/reset-send/" not in html
    assert "/download-pdf/" in html


@pytest.mark.django_db
def test_run_sent_shows_none_of_the_four_report_buttons(
    admin_client_logged_in, installation, suite
):
    run = _run(installation, suite, AuditRun.ReportState.SENT)
    html = _run_change_html(admin_client_logged_in, run)
    assert "/approve-report/" not in html
    assert "/reject-report/" not in html
    assert "/resend-report/" not in html
    assert "/reset-send/" not in html
    assert "/download-pdf/" in html


@pytest.mark.django_db
def test_run_sending_fresh_hides_reset_send(admin_client_logged_in, installation, suite):
    run = _run(installation, suite, AuditRun.ReportState.SENDING)
    run.sending_since = timezone.now()
    run.save(update_fields=["sending_since"])
    html = _run_change_html(admin_client_logged_in, run)
    assert "/reset-send/" not in html
    assert "/download-pdf/" in html


@pytest.mark.django_db
def test_run_sending_stale_shows_reset_send(admin_client_logged_in, installation, suite):
    run = _run(installation, suite, AuditRun.ReportState.SENDING)
    run.sending_since = timezone.now() - timedelta(hours=2)
    run.save(update_fields=["sending_since"])
    html = _run_change_html(admin_client_logged_in, run)
    assert "/reset-send/" in html
    assert "/download-pdf/" in html
