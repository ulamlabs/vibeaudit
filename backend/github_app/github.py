"""
GitHub App integration using PyGithub.
"""

import requests
from django.conf import settings
from github import Auth, GithubIntegration


def get_github_integration() -> GithubIntegration:
    """
    Factory function to create a GithubIntegration instance.
    Reads app ID and private key from settings.
    """
    app_id = settings.GITHUB_APP_ID
    private_key = settings.GITHUB_APP_PRIVATE_KEY

    if not app_id or not private_key:
        raise ValueError(
            "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY must be set in settings"
        )

    auth = Auth.AppAuth(app_id, private_key)
    return GithubIntegration(auth=auth)


def get_installation_token(installation_id: int) -> str:
    """
    Get an installation access token for the given installation ID.
    """
    gi = get_github_integration()
    token_obj = gi.get_access_token(installation_id)
    return token_obj.token


def list_repos(installation_id: int) -> list[dict]:
    """
    List all repositories accessible to the given installation.
    Returns a list of dicts with keys: id, full_name, private, description.
    """
    token = get_installation_token(installation_id)
    response = requests.get(
        "https://api.github.com/installation/repositories",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    response.raise_for_status()
    repositories = response.json().get("repositories", [])

    repos = []
    for repo in repositories:
        repos.append(
            {
                "id": repo.get("id"),
                "full_name": repo.get("full_name", ""),
                "private": repo.get("private", False),
                "description": repo.get("description") or "",
            }
        )

    return repos


def delete_installation(installation_id: int) -> None:
    """
    Delete a GitHub App installation.
    Uses app-level JWT authentication.
    """
    gi = get_github_integration()

    # Try to use the internal requester if available
    try:
        # This is a bit of a hack, but PyGithub doesn't expose this endpoint directly
        # We use the internal requester which has the JWT pre-configured
        gi._Requester__requester.requestJsonAndCheck(
            "DELETE",
            f"https://api.github.com/app/installations/{installation_id}",
        )
    except (AttributeError, Exception):
        # Fallback: use integration-generated JWT and call the endpoint directly.
        token = gi.create_jwt(expiration=600)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        response = requests.delete(
            f"https://api.github.com/app/installations/{installation_id}",
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
