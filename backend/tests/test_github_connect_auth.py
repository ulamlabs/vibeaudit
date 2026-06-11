import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


def make_client(authenticated: bool) -> APIClient:
    client = APIClient()
    if authenticated:
        user = get_user_model().objects.create_user(username="tester", password="pass-123")
        client.force_login(user)
    return client


@pytest.mark.parametrize(
    "allow_unauthenticated, authenticated, expected_status",
    [
        (False, False, 403),  # blocked: setting off, anonymous
        (False, True, 302),   # allowed: setting off, authenticated
        (True, False, 302),   # allowed: setting on, anonymous
    ],
)
@pytest.mark.django_db
def test_connect_authentication(settings, allow_unauthenticated, authenticated, expected_status):
    settings.ALLOW_UNAUTHENTICATED_AUDIT = allow_unauthenticated
    client = make_client(authenticated)
    response = client.get("/api/github/connect")
    assert response.status_code == expected_status


@pytest.mark.parametrize(
    "allow_unauthenticated, authenticated, expected_status",
    [
        (False, False, 403),  # blocked: setting off, anonymous
        (False, True, 400),   # auth passes, invalid state → 400
        (True, False, 400),   # auth passes, invalid state → 400
    ],
)
@pytest.mark.django_db
def test_setup_authentication(settings, allow_unauthenticated, authenticated, expected_status):
    settings.ALLOW_UNAUTHENTICATED_AUDIT = allow_unauthenticated
    client = make_client(authenticated)
    # No state stored in session → "Invalid state parameter" (400), not auth error (403)
    response = client.get("/api/github/setup?state=fake&installation_id=1")
    assert response.status_code == expected_status
