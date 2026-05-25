"""
Audit job views.
"""
from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditJob
from audit.serializers import AuditJobSerializer, StartAuditSerializer
from github_app.github import InstallationNotFoundError, repo_is_accessible
from github_app.models import Installation


class StartAuditView(APIView):
    """
    Start a new audit job for a repository.
    Requires installation_id in session.
    If ALLOW_ANONYMOUS_AUDIT is False, requires authenticated user.
    """

    def post(self, request):
        # Check if installation_id is in session
        installation_id = request.session.get("installation_id")
        if not installation_id:
            return Response({"error": "Not authorized"}, status=401)

        # Check if user auth is required
        if not settings.ALLOW_ANONYMOUS_AUDIT:
            if not request.user.is_authenticated:
                return Response({"error": "Authentication required"}, status=403)

        # Validate request body
        serializer = StartAuditSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        # Create the audit job
        repo_full_name = serializer.validated_data["repo_full_name"]
        email = serializer.validated_data["email"]

        try:
            installation = Installation.objects.get(
                installation_id=installation_id,
                remote_deleted_at__isnull=True,
            )
        except Installation.DoesNotExist:
            return Response({"error": "Installation not found or has been deleted"}, status=401)

        try:
            repo_allowed = repo_is_accessible(installation.installation_id, repo_full_name)
        except InstallationNotFoundError:
            installation.mark_remote_deleted()
            return Response({"error": "Installation no longer exists on GitHub"}, status=401)
        except Exception:
            return Response(
                {"error": "Failed to validate repository access. Please try again."},
                status=503,
            )

        if not repo_allowed:
            return Response(
                {"error": "Repository is not accessible to this GitHub installation."},
                status=400,
            )

        audit_job = AuditJob.objects.create(
            installation=installation,
            repo_full_name=repo_full_name,
            email=email,
            state=AuditJob.State.PENDING,
        )

        # Return the created job
        output_serializer = AuditJobSerializer(audit_job)
        return Response(output_serializer.data, status=201)
