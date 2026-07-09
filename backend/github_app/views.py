"""
GitHub App OAuth views.
"""

import secrets
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from rest_framework.decorators import api_view
from rest_framework.decorators import authentication_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.authentication import AuditAuthentication

from github_app.github import (
    InstallationNotFoundError,
    check_installation_active,
    get_installation_info,
    list_repos,
)
from github_app.models import Installation
from github_app.permissions import (
    ActiveInstallationPermission,
    mark_installation_gone,
)
from github_app.return_to import build_state, parse_state, is_allowed_return_to
from github_app.serializers import InstallationSerializer, RepoSerializer


@api_view(["GET"])
@authentication_classes([AuditAuthentication])
def connect(request: HttpRequest) -> HttpResponse:
    """
    Initiate GitHub App installation flow.
    Generates a one-time nonce (stored in session) and an install `state` that
    also carries an optional, allowlisted `return_to` for the post-install
    redirect. Redirects to GitHub's installation page.
    """
    nonce = secrets.token_urlsafe(32)
    request.session["github_oauth_state"] = nonce
    # Force session persistence before redirect so callback can read nonce.
    request.session.save()

    return_to = request.GET.get("return_to")
    if not is_allowed_return_to(return_to, settings.AUDIT_ALLOWED_RETURN_ORIGINS):
        return_to = None

    state = build_state(nonce, return_to)

    github_app_slug = settings.GITHUB_APP_SLUG
    github_install_url = f"https://github.com/apps/{github_app_slug}/installations/new?state={quote(state)}"

    return HttpResponseRedirect(github_install_url)


@api_view(["GET"])
@authentication_classes([AuditAuthentication])
def setup(request: HttpRequest) -> HttpResponse:
    """
    GitHub App installation callback. Validates the nonce carried in `state`,
    creates/fetches the Installation, stores installation_id in session, and
    redirects to the allowlisted `return_to` (or `/repo-picker` by default).
    """
    raw_state = request.GET.get("state")
    installation_id = request.GET.get("installation_id")

    nonce, return_to = parse_state(raw_state) if raw_state else (None, None)

    stored_nonce = request.session.get("github_oauth_state")
    if not nonce or nonce != stored_nonce:
        return HttpResponse("Invalid state parameter", status=400)

    if not installation_id:
        return HttpResponse("Missing installation_id", status=400)

    try:
        installation_id_int = int(installation_id)
        if installation_id_int <= 0:
            raise ValueError()
    except ValueError, TypeError:
        return HttpResponse(
            "Invalid installation_id (must be positive integer)", status=400
        )

    try:
        info = get_installation_info(installation_id_int)
    except InstallationNotFoundError:
        return HttpResponse("Installation not found on GitHub", status=400)

    installation, created = Installation.objects.get_or_create(
        installation_id=installation_id_int,
        defaults={
            "account_login": info.account_login,
            "account_type": info.account_type,
        },
    )
    if not created:
        installation.reactivate(
            account_login=info.account_login,
            account_type=info.account_type,
        )

    # An authenticated user owns the installations they connect, so we can
    # resolve them by ownership on any later session or device. Anonymous funnel
    # visitors have no owner and stay session-tracked (below).
    if request.user.is_authenticated and installation.owner_id != request.user.pk:
        installation.owner = request.user
        installation.save(update_fields=["owner"])

    request.session["installation_id"] = installation_id_int

    # Consume the one-time nonce.
    if request.session.get("github_oauth_state") == nonce:
        del request.session["github_oauth_state"]
    request.session.save()

    if is_allowed_return_to(return_to, settings.AUDIT_ALLOWED_RETURN_ORIGINS):
        return redirect(return_to)
    return redirect("/repo-picker")


class InstallationsView(APIView):
    """
    GET  /api/github/installations — return the current installation for this session.
    Lazily marks remote_deleted_at if GitHub reports the installation is gone.
    """

    authentication_classes = [AuditAuthentication]
    permission_classes = [ActiveInstallationPermission]

    def get(self, request):
        installation = request.installation

        # Lazy verification against GitHub.
        try:
            check_installation_active(installation.installation_id)
        except InstallationNotFoundError:
            mark_installation_gone(installation)
        except Exception:
            # Network errors etc. — don't penalise the user; treat as still active.
            pass

        has_active_jobs = installation.has_active_audit_jobs()

        serializer = InstallationSerializer(
            installation, context={"has_active_jobs": has_active_jobs}
        )
        return Response(serializer.data)


class InstallationDeleteView(APIView):
    """
    DELETE /api/github/installations/<installation_id> — uninstall and soft-delete.
    """

    authentication_classes = [AuditAuthentication]
    permission_classes = [ActiveInstallationPermission]

    def delete(self, request, installation_id: int):
        installation = request.installation
        if installation.installation_id != installation_id:
            return Response({"error": "Forbidden"}, status=403)

        # Block if active jobs are in progress.
        if installation.has_active_audit_jobs():
            return Response(
                {
                    "error": "Cannot delete installation while audit jobs are in progress"
                },
                status=400,
            )

        # Uninstall from GitHub and soft-delete locally.
        installation.uninstall()

        del request.session["installation_id"]
        request.session.save()

        return Response(status=204)


class ReposView(APIView):
    """
    List repositories accessible to the currently installed GitHub App.
    Resolves the installation via ActiveInstallationPermission: the session's
    installation_id, or (for an authenticated user) their owned installation.
    """

    authentication_classes = [AuditAuthentication]
    permission_classes = [ActiveInstallationPermission]

    def get(self, request):
        installation = request.installation

        try:
            repos = list_repos(installation.installation_id)
            serializer = RepoSerializer(repos, many=True)
            return Response({"repos": serializer.data})
        except InstallationNotFoundError:
            mark_installation_gone(installation)
        except Exception:
            return Response(
                {"error": "Failed to load repositories. Please try again."}, status=503
            )
