from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from audit.models import AuditJob
from github_app.models import Installation

VALID_PAYLOAD = {"repo_full_name": "octocat/hello-world", "email": "user@example.com"}


@pytest.fixture(autouse=True)
def allow_unauthenticated(settings):
    settings.ALLOW_UNAUTHENTICATED_AUDIT = True


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=42, account_login="octocat", account_type="User"
    )


def client_with_session(installation_id: int) -> APIClient:
    client = APIClient()
    session = client.session
    session["installation_id"] = installation_id
    session.save()
    return client


@pytest.mark.django_db
def test_session_empty_without_submissions():
    resp = APIClient().get("/api/audit/session")
    assert resp.status_code == 200
    assert resp.json() == {"submitted_count": 0, "active_count": 0}


@pytest.mark.django_db
def test_submitting_records_job_on_session(installation):
    client = client_with_session(installation.installation_id)
    with patch("audit.views.repo_is_accessible", return_value=True):
        resp = client.post("/api/audit/start", VALID_PAYLOAD, format="json")
    assert resp.status_code == 201

    resp = client.get("/api/audit/session")
    assert resp.json() == {"submitted_count": 1, "active_count": 1}


@pytest.mark.django_db
def test_session_counts_persist_after_installation_remote_deleted(installation):
    client = client_with_session(installation.installation_id)
    with patch("audit.views.repo_is_accessible", return_value=True):
        client.post("/api/audit/start", VALID_PAYLOAD, format="json")

    # Installation auto-deleted after clone: /installations now 404s...
    installation.mark_remote_deleted()
    assert client.get("/api/github/installations").status_code == 404

    # ...but the session still knows an audit was submitted.
    resp = client.get("/api/audit/session")
    assert resp.json() == {"submitted_count": 1, "active_count": 1}


@pytest.mark.django_db
def test_active_count_drops_when_job_finishes(installation):
    client = client_with_session(installation.installation_id)
    with patch("audit.views.repo_is_accessible", return_value=True):
        client.post("/api/audit/start", VALID_PAYLOAD, format="json")

    job = AuditJob.objects.get()
    job.state = AuditJob.State.CLOSED
    job.save(update_fields=["state"])

    resp = client.get("/api/audit/session")
    assert resp.json() == {"submitted_count": 1, "active_count": 0}
