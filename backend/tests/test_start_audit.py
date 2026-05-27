from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from audit.models import AuditJob
from github_app.github import InstallationNotFoundError
from github_app.models import Installation

VALID_PAYLOAD = {"repo_full_name": "octocat/hello-world", "email": "user@example.com"}
START_URL = "/api/audit/start"


def client_with_session(installation_id: int) -> APIClient:
    client = APIClient()
    session = client.session
    session["installation_id"] = installation_id
    session.save()
    return client


@pytest.fixture(autouse=True)
def allow_unauthenticated(settings):
    settings.ALLOW_UNAUTHENTICATED_AUDIT = True


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=42,
        account_login="octocat",
        account_type="User",
    )


@pytest.mark.django_db
def test_missing_session_returns_401(installation):
    client = APIClient()  # no installation_id in session
    with patch("audit.views.repo_is_accessible", return_value=True):
        response = client.post(START_URL, VALID_PAYLOAD, format="json")
    assert response.status_code == 401
    assert response.json() == {"error": "Not authorized"}


@pytest.mark.django_db
def test_invalid_repo_format_returns_400(installation):
    client = client_with_session(installation.installation_id)
    response = client.post(
        START_URL,
        {"repo_full_name": "no-slash-here", "email": "user@example.com"},
        format="json",
    )
    assert response.status_code == 400
    assert "repo_full_name" in response.json()


@pytest.mark.django_db
def test_invalid_email_returns_400(installation):
    client = client_with_session(installation.installation_id)
    response = client.post(
        START_URL,
        {"repo_full_name": "octocat/hello-world", "email": "not-an-email"},
        format="json",
    )
    assert response.status_code == 400
    assert "email" in response.json()


@pytest.mark.django_db
def test_soft_deleted_installation_returns_404(installation):
    installation.mark_remote_deleted()
    client = client_with_session(installation.installation_id)
    with patch("audit.views.repo_is_accessible", return_value=True):
        response = client.post(START_URL, VALID_PAYLOAD, format="json")
    assert response.status_code == 404


@pytest.mark.django_db
def test_repo_not_accessible_returns_400(installation):
    client = client_with_session(installation.installation_id)
    with patch("audit.views.repo_is_accessible", return_value=False):
        response = client.post(START_URL, VALID_PAYLOAD, format="json")
    assert response.status_code == 400
    assert response.json() == {
        "error": "Repository is not accessible to this GitHub installation."
    }
    assert AuditJob.objects.count() == 0


@pytest.mark.django_db
def test_installation_gone_on_github_returns_401_and_marks_deleted(installation):
    client = client_with_session(installation.installation_id)
    with patch(
        "audit.views.repo_is_accessible",
        side_effect=InstallationNotFoundError(installation.installation_id),
    ):
        response = client.post(START_URL, VALID_PAYLOAD, format="json")
    assert response.status_code == 401
    assert response.json() == {"error": "Installation no longer exists on GitHub"}
    installation.refresh_from_db()
    assert installation.remote_deleted_at is not None
    assert AuditJob.objects.count() == 0


@pytest.mark.django_db
def test_github_api_error_returns_503(installation):
    client = client_with_session(installation.installation_id)
    with patch(
        "audit.views.repo_is_accessible",
        side_effect=Exception("network error"),
    ):
        response = client.post(START_URL, VALID_PAYLOAD, format="json")
    assert response.status_code == 503
    assert AuditJob.objects.count() == 0


@pytest.mark.django_db
def test_successful_audit_creation_returns_201(installation):
    client = client_with_session(installation.installation_id)
    with patch("audit.views.repo_is_accessible", return_value=True):
        response = client.post(START_URL, VALID_PAYLOAD, format="json")
    assert response.status_code == 201
    data = response.json()
    assert data["repo_full_name"] == "octocat/hello-world"
    assert data["email"] == "user@example.com"
    assert data["state"] == AuditJob.State.PENDING
    assert AuditJob.objects.count() == 1
