import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from github_app.models import Installation


@pytest.mark.django_db
def test_start_audit_post_succeeds_without_csrf_token(settings, monkeypatch):
    """CSRF is skipped when AUDIT_ENFORCE_CSRF=False (default).

    DRF's SessionAuthentication only calls enforce_csrf when there is an
    active authenticated user, so we must log in a user to exercise the
    override. With enforce_csrf_checks=True on the client and no CSRF token
    sent, a plain SessionAuthentication would return 403; with
    AUDIT_ENFORCE_CSRF=False it must return 201 instead.
    """
    settings.ALLOW_UNAUTHENTICATED_AUDIT = True
    settings.AUDIT_ENFORCE_CSRF = False
    installation = Installation.objects.create(
        installation_id=4242, account_login="octo", account_type="User"
    )
    monkeypatch.setattr(
        "audit.views.repo_is_accessible", lambda installation_id, repo: True
    )
    monkeypatch.setattr(
        "audit.views.clone_repo",
        type("T", (), {"delay": staticmethod(lambda pk: None)}),
    )

    # Log in a user so DRF's SessionAuthentication.authenticate() returns a
    # user tuple and actually calls enforce_csrf before proceeding.
    user = get_user_model().objects.create_user(username="tester", password="pass")
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)

    session = client.session
    session["installation_id"] = installation.installation_id
    session.save()

    response = client.post(
        "/api/audit/start",
        {"repo_full_name": "octo/repo", "email": "x@example.com"},
        format="json",
    )
    # Without the no-op override this returns 403 (CSRF Failed).
    assert response.status_code == 201, response.content
