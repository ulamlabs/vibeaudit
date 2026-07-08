"""
Shared session-installation access control for the audit funnel API.

Status-code convention (mirrored by the frontend route guards):
- 401 — the session carries no installation_id (user must (re)connect).
- 404 — the installation is unknown locally, soft-deleted, or confirmed gone
  on GitHub (lazy detection).
"""

from typing import NoReturn

from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

from github_app.models import Installation


class NotAuthorizedError(APIException):
    status_code = 401
    default_detail = {"error": "Not authorized"}


class InstallationGoneError(APIException):
    status_code = 404
    default_detail = {"error": "Installation not found"}


class ActiveInstallationPermission(BasePermission):
    """
    Require the request to resolve to a locally-active GitHub App installation,
    stashed on ``request.installation``.

    Resolution order:
    1. The session's ``installation_id`` — the anonymous funnel's tracking.
    2. For an authenticated user, their most-recently connected active owned
       installation. This is durable across sessions and devices, so a logged-in
       user (e.g. staff on a new device) resolves without a session at all.
    """

    def has_permission(self, request, view) -> bool:
        installation_id = request.session.get("installation_id")
        if installation_id:
            try:
                request.installation = Installation.objects.get(
                    installation_id=installation_id,
                    remote_deleted_at__isnull=True,
                )
                return True
            except Installation.DoesNotExist:
                # Session points at a since-deleted install. Authenticated users
                # fall through to ownership; anonymous visitors get a 404.
                if not request.user.is_authenticated:
                    raise InstallationGoneError()

        if request.user.is_authenticated:
            installation = (
                Installation.objects.filter(
                    owner=request.user, remote_deleted_at__isnull=True
                )
                .order_by("-created_at")
                .first()
            )
            if installation is not None:
                request.installation = installation
                return True
            raise InstallationGoneError()

        raise NotAuthorizedError()


def mark_installation_gone(installation: Installation) -> NoReturn:
    """
    Record that GitHub reported the installation missing (lazy detection) and
    raise the canonical 404 for it.
    """
    installation.mark_remote_deleted()
    raise InstallationGoneError(
        {"error": "Installation no longer exists on GitHub"}
    )
