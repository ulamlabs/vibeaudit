"""
GitHub App OAuth views.
"""
import secrets

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from rest_framework.response import Response
from rest_framework.views import APIView

from github_app.github import list_repos
from github_app.serializers import RepoSerializer


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
    Validates state, stores installation_id in session, and redirects to repo picker.
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

    # Store installation_id in session and consume state (one-time use)
    request.session["installation_id"] = installation_id_int

    if request.session.get("github_oauth_state") == state:
        del request.session["github_oauth_state"]
    request.session.save()

    return redirect("/repo-picker")


class ReposView(APIView):
    """
    List repositories accessible to the currently installed GitHub App.
    Requires installation_id in session.
    """

    def get(self, request):
        installation_id = request.session.get("installation_id")
        if not installation_id:
            return Response({"error": "Not authorized"}, status=401)

        try:
            repos = list_repos(installation_id)
            serializer = RepoSerializer(repos, many=True)
            return Response({"repos": serializer.data})
        except Exception as e:
            return Response({"error": str(e)}, status=500)
