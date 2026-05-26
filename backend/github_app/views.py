"""
GitHub App OAuth views.
"""
import secrets

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
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
from github_app.serializers import InstallationSerializer, RepoSerializer


def connect(request: HttpRequest) -> HttpResponse:
    """
    Initiate GitHub App installation flow.
    Generates a CSRF state token and redirects to GitHub's installation page.
    """
    state = secrets.token_urlsafe(32)
    request.session["github_oauth_state"] = state

    # Force session persistence before redirect so callback can read state deterministically.
    request.session.save()

    github_app_slug = settings.GITHUB_APP_SLUG
    github_install_url = (
        f"https://github.com/apps/{github_app_slug}/installations/new?state={state}"
    )

    return HttpResponseRedirect(github_install_url)


def setup(request: HttpRequest) -> HttpResponse:
    """
    GitHub App installation callback.
    Validates state, creates/fetches the Installation record, stores installation_id
    in session, and redirects to repo picker.
    """
    state = request.GET.get("state")
    installation_id = request.GET.get("installation_id")

    # Validate state
    stored_state = request.session.get("github_oauth_state")
    if not state or state != stored_state:
        return HttpResponse("Invalid state parameter", status=400)

    # Validate installation_id
    if not installation_id:
        return HttpResponse("Missing installation_id", status=400)

    try:
        installation_id_int = int(installation_id)
        if installation_id_int <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        return HttpResponse("Invalid installation_id (must be positive integer)", status=400)

    # Fetch installation metadata from GitHub and persist locally.
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

    # Store installation_id in session and consume state (one-time use).
    request.session["installation_id"] = installation_id_int

    if request.session.get("github_oauth_state") == state:
        del request.session["github_oauth_state"]
    request.session.save()

    return redirect("/repo-picker")


class InstallationsView(APIView):
    """
    GET  /api/github/installations — return the current installation for this session.
    Lazily marks remote_deleted_at if GitHub reports the installation is gone.
    """

    authentication_classes = [AuditAuthentication]

    def get(self, request):
        installation_id = request.session.get("installation_id")
        if not installation_id:
            return Response({"error": "Not authorized"}, status=401)

        try:
            installation = Installation.objects.get(
                installation_id=installation_id,
                remote_deleted_at__isnull=True,
            )
        except Installation.DoesNotExist:
            return Response({"error": "Installation not found"}, status=404)

        # Lazy verification against GitHub.
        try:
            check_installation_active(installation_id)
        except InstallationNotFoundError:
            installation.mark_remote_deleted()
            return Response({"error": "Installation no longer exists on GitHub"}, status=404)
        except Exception:
            # Network errors etc. — don't penalise the user; treat as still active.
            pass

        has_active_jobs = installation.audit_jobs.filter(
            state__in=["pending", "cloning", "running"]
        ).exists()

        serializer = InstallationSerializer(
            installation, context={"has_active_jobs": has_active_jobs}
        )
        return Response(serializer.data)


class InstallationDeleteView(APIView):
    """
    DELETE /api/github/installations/<installation_id> — uninstall and soft-delete.
    """

    authentication_classes = [AuditAuthentication]

    def delete(self, request, installation_id: int):
        session_installation_id = request.session.get("installation_id")
        if not session_installation_id:
            return Response({"error": "Not authorized"}, status=401)

        if session_installation_id != installation_id:
            return Response({"error": "Forbidden"}, status=403)

        try:
            installation = Installation.objects.get(installation_id=installation_id)
        except Installation.DoesNotExist:
            return Response({"error": "Installation not found"}, status=404)

        # Block if active jobs are in progress.
        active_jobs = installation.audit_jobs.filter(
            state__in=["pending", "cloning", "running"]
        ).exists()
        if active_jobs:
            return Response(
                {"error": "Cannot delete installation while audit jobs are in progress"},
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
    Requires installation_id in session.
    """

    authentication_classes = [AuditAuthentication]

    def get(self, request):
        installation_id = request.session.get("installation_id")
        if not installation_id:
            return Response({"error": "Not authorized"}, status=401)

        try:
            installation = Installation.objects.get(
                installation_id=installation_id, remote_deleted_at__isnull=True
            )
        except Installation.DoesNotExist:
            return Response({"error": "Installation not found or has been deleted"}, status=401)

        try:
            repos = list_repos(installation_id)
            serializer = RepoSerializer(repos, many=True)
            return Response({"repos": serializer.data})
        except InstallationNotFoundError:
            installation.mark_remote_deleted()
            return Response({"error": "Installation no longer exists on GitHub"}, status=401)
        except Exception:
            return Response({"error": "Failed to load repositories. Please try again."}, status=503)
