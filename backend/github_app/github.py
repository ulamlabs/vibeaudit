"""
GitHub App integration using PyGithub.
"""

import requests
from django.conf import settings
from github import Auth, GithubException, GithubIntegration


class InstallationNotFoundError(Exception):
    """Raised when GitHub reports an installation does not exist (404)."""


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


def _get_app_jwt() -> str:
    """Return a short-lived JWT for GitHub App-level API calls."""
    gi = get_github_integration()
    return gi.create_jwt(expiration=600)


def _app_api_headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_app_jwt()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_installation_info(installation_id: int) -> dict:
    """
    Fetch account_login and account_type for an installation from the GitHub API.
    Returns {"account_login": str, "account_type": str}.
    Raises InstallationNotFoundError if GitHub returns 404.
    """
    response = requests.get(
        f"https://api.github.com/app/installations/{installation_id}",
        headers=_app_api_headers(),
        timeout=30,
    )
    if response.status_code == 404:
        raise InstallationNotFoundError(installation_id)
    response.raise_for_status()
    data = response.json()
    account = data.get("account", {})
    return {
        "account_login": account.get("login", ""),
        "account_type": account.get("type", "User"),
    }


def check_installation_active(installation_id: int) -> None:
    """
    Verify that a GitHub App installation still exists.
    Raises InstallationNotFoundError if GitHub returns 404.
    No database side effects — the caller is responsible for updating remote_deleted_at.
    """
    response = requests.get(
        f"https://api.github.com/app/installations/{installation_id}",
        headers=_app_api_headers(),
        timeout=30,
    )
    if response.status_code == 404:
        raise InstallationNotFoundError(installation_id)
    response.raise_for_status()


def get_installation_token(installation_id: int) -> str:
    """
    Get an installation access token for the given installation ID.
    Raises InstallationNotFoundError if the installation no longer exists on GitHub.
    """
    gi = get_github_integration()
    try:
        token_obj = gi.get_access_token(installation_id)
    except GithubException as e:
        if e.status == 404:
            raise InstallationNotFoundError(installation_id) from e
        raise
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


def repo_is_accessible(installation_id: int, repo_full_name: str) -> bool:
    """
    Check whether the installation can access the given repository.
    Returns False when the repository does not exist or is not granted to the installation.
    Raises InstallationNotFoundError if the installation no longer exists on GitHub.
    """
    token = get_installation_token(installation_id)
    response = requests.get(
        f"https://api.github.com/repos/{repo_full_name}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def delete_installation(installation_id: int) -> None:
    """
    Delete a GitHub App installation via the GitHub API.
    Raises InstallationNotFoundError if the installation is already gone (404).
    """
    response = requests.delete(
        f"https://api.github.com/app/installations/{installation_id}",
        headers=_app_api_headers(),
        timeout=30,
    )
    if response.status_code == 404:
        raise InstallationNotFoundError(installation_id)
    response.raise_for_status()
