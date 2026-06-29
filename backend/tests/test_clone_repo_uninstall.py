from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from audit.models import AuditJob
from audit.tasks import clone_repo
from github_app.models import Installation


@pytest.fixture(autouse=True)
def allow_anon(settings, tmp_path):
    settings.ALLOW_UNAUTHENTICATED_AUDIT = True
    settings.REPOS_DIR = str(tmp_path)


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=7, account_login="octocat", account_type="User"
    )


@pytest.fixture
def pending_job(installation):
    return AuditJob.objects.create(
        installation=installation,
        repo_full_name="octocat/hello",
        email="x@y.z",
        state=AuditJob.State.PENDING,
    )


@pytest.mark.django_db
def test_clone_repo_uninstalls_installation_after_clone(pending_job):
    with (
        patch("audit.tasks.get_installation_token", return_value="tok"),
        patch("audit.tasks.git.Repo.clone_from"),
        patch("audit.tasks.send_new_submission_notification"),
        patch("github_app.models.delete_installation") as delete_installation,
    ):
        clone_repo(pending_job.pk)

    delete_installation.assert_called_once_with(7)

    pending_job.refresh_from_db()
    assert pending_job.state == AuditJob.State.AWAITING_APPROVAL

    pending_job.installation.refresh_from_db()
    assert pending_job.installation.remote_deleted_at is not None


@pytest.mark.django_db
def test_clone_repo_uninstalls_even_with_keep_sources(installation):
    # keep_sources retains the local clone, but re-runs reuse it and never need
    # GitHub again — so we still drop access after the clone.
    job = AuditJob.objects.create(
        installation=installation,
        repo_full_name="octocat/hello",
        email="x@y.z",
        state=AuditJob.State.PENDING,
        keep_sources=True,
    )
    with (
        patch("audit.tasks.get_installation_token", return_value="tok"),
        patch("audit.tasks.git.Repo.clone_from"),
        patch("audit.tasks.send_new_submission_notification"),
        patch("github_app.models.delete_installation") as delete_installation,
    ):
        clone_repo(job.pk)

    delete_installation.assert_called_once_with(installation.installation_id)
    installation.refresh_from_db()
    assert installation.remote_deleted_at is not None


@pytest.mark.django_db
def test_uninstalled_installation_is_404_for_session(pending_job):
    with (
        patch("audit.tasks.get_installation_token", return_value="tok"),
        patch("audit.tasks.git.Repo.clone_from"),
        patch("audit.tasks.send_new_submission_notification"),
        patch("github_app.models.delete_installation"),
    ):
        clone_repo(pending_job.pk)

    # A session still carrying the now-deleted installation_id must read as gone.
    client = APIClient()
    session = client.session
    session["installation_id"] = pending_job.installation.installation_id
    session.save()

    resp = client.get("/api/github/installations")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_clone_repo_uninstall_failure_does_not_fail_audit(pending_job):
    # A failed uninstall (e.g. GitHub network error) must not fail the audit:
    # the clone succeeded and the job stays AWAITING_APPROVAL.
    with (
        patch("audit.tasks.get_installation_token", return_value="tok"),
        patch("audit.tasks.git.Repo.clone_from"),
        patch("audit.tasks.send_new_submission_notification") as notify,
        patch(
            "github_app.models.delete_installation",
            side_effect=RuntimeError("github down"),
        ),
    ):
        clone_repo(pending_job.pk)

    pending_job.refresh_from_db()
    assert pending_job.state == AuditJob.State.AWAITING_APPROVAL
    notify.assert_called_once()
