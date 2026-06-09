"""Tests for /api/me and the staff-only keep_sources gating on audit submission."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from audit.models import AuditJob
from github_app.models import Installation

START_URL = "/api/audit/start"
ME_URL = "/api/me"
PAYLOAD = {"repo_full_name": "octocat/hello-world", "email": "user@example.com"}


@pytest.fixture(autouse=True)
def allow_unauthenticated(settings):
    settings.ALLOW_UNAUTHENTICATED_AUDIT = True


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=42, account_login="octocat", account_type="User"
    )


def _client(installation_id=None, user=None):
    client = APIClient()
    if user is not None:
        client.force_login(user)
    if installation_id is not None:
        session = client.session
        session["installation_id"] = installation_id
        session.save()
    return client


@pytest.mark.django_db
def test_me_anonymous_is_denied():
    resp = APIClient().get(ME_URL)
    assert resp.status_code == 403


@pytest.mark.django_db
def test_me_authenticated_non_staff():
    user = get_user_model().objects.create_user("bob", password="x", is_staff=False)
    resp = _client(user=user).get(ME_URL)
    assert resp.status_code == 200
    assert resp.json() == {"is_staff": False}


@pytest.mark.django_db
def test_me_staff_user_reports_is_staff():
    user = get_user_model().objects.create_user("admin", password="x", is_staff=True)
    resp = _client(user=user).get(ME_URL)
    assert resp.status_code == 200
    assert resp.json() == {"is_staff": True}


@pytest.mark.django_db
def test_anonymous_submit_forces_keep_sources_false(installation):
    client = _client(installation_id=installation.installation_id)
    with patch("audit.views.repo_is_accessible", return_value=True):
        resp = client.post(START_URL, {**PAYLOAD, "keep_sources": True}, format="json")
    assert resp.status_code == 201
    job = AuditJob.objects.get(pk=resp.json()["id"])
    assert job.keep_sources is False  # non-staff cannot keep sources


@pytest.mark.django_db
def test_staff_submit_honors_keep_sources(installation):
    user = get_user_model().objects.create_user("admin", password="x", is_staff=True)
    client = _client(installation_id=installation.installation_id, user=user)
    with patch("audit.views.repo_is_accessible", return_value=True):
        resp = client.post(START_URL, {**PAYLOAD, "keep_sources": True}, format="json")
    job = AuditJob.objects.get(pk=resp.json()["id"])
    assert job.keep_sources is True


@pytest.mark.django_db
def test_staff_submit_can_opt_out_of_keep_sources(installation):
    user = get_user_model().objects.create_user("admin", password="x", is_staff=True)
    client = _client(installation_id=installation.installation_id, user=user)
    with patch("audit.views.repo_is_accessible", return_value=True):
        resp = client.post(START_URL, {**PAYLOAD, "keep_sources": False}, format="json")
    job = AuditJob.objects.get(pk=resp.json()["id"])
    assert job.keep_sources is False


@pytest.mark.django_db
def test_staff_submit_defaults_to_keep_sources_false(installation):
    user = get_user_model().objects.create_user("admin", password="x", is_staff=True)
    client = _client(installation_id=installation.installation_id, user=user)
    with patch("audit.views.repo_is_accessible", return_value=True):
        resp = client.post(START_URL, PAYLOAD, format="json")
    job = AuditJob.objects.get(pk=resp.json()["id"])
    assert job.keep_sources is False  # defaults to False for all users
