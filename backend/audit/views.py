"""
Audit job views.
"""

from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.authentication import AuditAuthentication
from audit.models import AuditJob
from audit.serializers import AuditJobSerializer, StartAuditSerializer
from github_app.github import InstallationNotFoundError, repo_is_accessible
from github_app.models import Installation


class StartAuditView(APIView):
    """
    Start a new audit job for a repository.
    Requires installation_id in session.
    If ALLOW_UNAUTHENTICATED_AUDIT is False, requires authenticated user.
    """

    authentication_classes = [AuditAuthentication]

    def post(self, request):
        # Check if installation_id is in session
        installation_id = request.session.get("installation_id")
        if not installation_id:
            return Response({"error": "Not authorized"}, status=401)

        # Validate request body
        serializer = StartAuditSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        # Create the audit job
        repo_full_name = serializer.validated_data["repo_full_name"]
        email = serializer.validated_data["email"]

        installation = get_object_or_404(
            Installation,
            installation_id=installation_id,
            remote_deleted_at__isnull=True,
        )

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
