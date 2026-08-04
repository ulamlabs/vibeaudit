from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from github_app.models import Installation
from github_app.return_to import parse_state


@pytest.fixture(autouse=True)
def allow_anon(settings):
    settings.ALLOW_UNAUTHENTICATED_AUDIT = True
    settings.AUDIT_ALLOWED_RETURN_ORIGINS = ["https://ulam.io"]
    settings.GITHUB_APP_SLUG = "vibeaudit"


def _state_from_redirect(location: str) -> str:
    # location: https://github.com/apps/<slug>/installations/new?state=<state>
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(location).query)["state"][0]


@pytest.mark.django_db
def test_connect_packs_allowed_return_to_into_state():
    client = APIClient()
    resp = client.get("/api/github/connect?return_to=https://ulam.io/x")
    assert resp.status_code == 302
    state = _state_from_redirect(resp["Location"])
    _, return_to = parse_state(state)
    assert return_to == "https://ulam.io/x"


@pytest.mark.django_db
def test_connect_drops_disallowed_return_to():
    client = APIClient()
    resp = client.get("/api/github/connect?return_to=https://evil.com/x")
    assert resp.status_code == 302
    state = _state_from_redirect(resp["Location"])
    _, return_to = parse_state(state)
    assert return_to is None


@pytest.mark.django_db
def test_setup_redirects_to_allowed_return_to():
    client = APIClient()
    # 1) connect to establish nonce + state in this client's session
    connect = client.get("/api/github/connect?return_to=https://ulam.io/done")
    state = _state_from_redirect(connect["Location"])

    info = type("I", (), {"account_login": "octo", "account_type": "User"})()
    with patch("github_app.views.get_installation_info", return_value=info):
        resp = client.get(f"/api/github/setup?state={state}&installation_id=99")

    assert resp.status_code == 302
    assert resp["Location"] == "https://ulam.io/done"
    assert Installation.objects.filter(installation_id=99).exists()


@pytest.mark.django_db
def test_setup_falls_back_to_repo_picker_without_return_to():
    client = APIClient()
    connect = client.get("/api/github/connect")  # no return_to
    state = _state_from_redirect(connect["Location"])

    info = type("I", (), {"account_login": "octo", "account_type": "User"})()
    with patch("github_app.views.get_installation_info", return_value=info):
        resp = client.get(f"/api/github/setup?state={state}&installation_id=100")

    assert resp.status_code == 302
    assert resp["Location"] == "/repo-picker"


@pytest.mark.django_db
def test_setup_rejects_bad_nonce():
    client = APIClient()
    client.get("/api/github/connect?return_to=https://ulam.io/x")
    resp = client.get("/api/github/setup?state=wrong-nonce&installation_id=1")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_setup_handles_non_integer_installation_id():
    # Regression: the old `except ValueError, TypeError:` was a SyntaxError.
    client = APIClient()
    connect = client.get("/api/github/connect")
    state = _state_from_redirect(connect["Location"])
    resp = client.get(f"/api/github/setup?state={state}&installation_id=abc")
    assert resp.status_code == 400
