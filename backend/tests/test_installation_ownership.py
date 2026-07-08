"""
Authenticated users resolve their installations and audits by ownership, so the
funnel works across a new session or device without a session installation_id.
Anonymous funnel visitors stay session-tracked (covered elsewhere).
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from audit.models import AuditJob
from github_app.models import Installation

REPOS_URL = "/api/github/repos"
SESSION_URL = "/api/audit/session"
START_URL = "/api/audit/start"
VALID_PAYLOAD = {"repo_full_name": "octocat/hello-world", "email": "user@example.com"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="alice", password="pw")


def _installation(owner=None, installation_id=42, **kwargs):
    return Installation.objects.create(
        installation_id=installation_id,
        account_login="octocat",
        account_type="User",
        owner=owner,
        **kwargs,
    )


def _job(installation, state=AuditJob.State.PENDING):
    return AuditJob.objects.create(
        installation=installation,
        repo_full_name="octocat/hello",
        email="x@y.z",
        state=state,
    )


@pytest.mark.django_db
def test_setup_claims_ownership_for_authenticated_user(user):
    client = APIClient()
    client.force_login(user)
    session = client.session
    session["github_oauth_state"] = "nonce"
    session.save()

    info = type("Info", (), {"account_login": "octocat", "account_type": "User"})()
    with (
        patch("github_app.views.get_installation_info", return_value=info),
        patch("github_app.views.build_state"),
        patch("github_app.views.parse_state", return_value=("nonce", None)),
    ):
        resp = client.get("/api/github/setup?state=x&installation_id=42")

    assert resp.status_code in (301, 302)
    installation = Installation.objects.get(installation_id=42)
    assert installation.owner_id == user.pk


@pytest.mark.django_db
def test_authenticated_user_resolves_owned_installation_without_session(user):
    """A new device (fresh session, still logged in) resolves by ownership."""
    _installation(owner=user)
    client = APIClient()
    client.force_login(user)  # no installation_id in session

    with patch("github_app.views.list_repos", return_value=[]):
        resp = client.get(REPOS_URL)

    assert resp.status_code == 200
    assert resp.json() == {"repos": []}


@pytest.mark.django_db
def test_authenticated_user_without_owned_installation_gets_404(user):
    client = APIClient()
    client.force_login(user)
    resp = client.get(REPOS_URL)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_owner_resolution_ignores_other_users_installations(user):
    other = get_user_model().objects.create_user(username="bob", password="pw")
    _installation(owner=other)
    client = APIClient()
    client.force_login(user)
    resp = client.get(REPOS_URL)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_session_counts_by_ownership_for_authenticated_user(user):
    """Counts survive a fresh session because they derive from ownership."""
    installation = _installation(owner=user)
    _job(installation, AuditJob.State.PENDING)
    _job(installation, AuditJob.State.CLOSED)

    client = APIClient()
    client.force_login(user)  # brand-new session, no audit_job_ids
    resp = client.get(SESSION_URL)

    assert resp.status_code == 200
    assert resp.json() == {"submitted_count": 2, "active_count": 1}


@pytest.mark.django_db
def test_authenticated_start_audit_resolves_by_ownership(user, settings):
    settings.ALLOW_UNAUTHENTICATED_AUDIT = False
    installation = _installation(owner=user)
    client = APIClient()
    client.force_login(user)  # no installation_id in session

    with patch("audit.views.repo_is_accessible", return_value=True):
        resp = client.post(START_URL, VALID_PAYLOAD, format="json")

    assert resp.status_code == 201
    job = AuditJob.objects.get()
    assert job.installation_id == installation.pk
