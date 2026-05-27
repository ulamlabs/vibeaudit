from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from audit.models import AuditJob
from github_app.models import Installation


def build_client_with_installation(installation_id: int) -> APIClient:
    client = APIClient()
    session = client.session
    session["installation_id"] = installation_id
    session.save()
    return client


@pytest.mark.parametrize(
    "allow_unauthenticated, authenticated, expected_status",
    [
        (False, False, 403),  # unauthenticated blocked when feature disabled
        (True, False, 201),   # unauthenticated allowed when feature enabled
        (False, True, 201),   # authenticated user bypasses setting
    ],
)
@pytest.mark.django_db
def test_start_audit_authentication(
    settings, allow_unauthenticated, authenticated, expected_status
) -> None:
    settings.ALLOW_UNAUTHENTICATED_AUDIT = allow_unauthenticated
    installation = Installation.objects.create(
        installation_id=123,
        account_login="octocat",
        account_type="User",
    )
    client = build_client_with_installation(installation.installation_id)
    if authenticated:
        user = get_user_model().objects.create_user(
            username="auditor",
            password="secret-pass-123",
        )
        client.force_login(user)

    with patch("audit.views.repo_is_accessible", return_value=True) as mock_accessible:
        response = client.post(
            "/api/audit/start",
            {"repo_full_name": "octocat/hello-world", "email": "user@example.com"},
            format="json",
        )

    assert response.status_code == expected_status
    if expected_status == 403:
        assert response.json() == {"detail": "Authentication required"}
        mock_accessible.assert_not_called()
        assert AuditJob.objects.count() == 0
    else:
        assert AuditJob.objects.count() == 1
