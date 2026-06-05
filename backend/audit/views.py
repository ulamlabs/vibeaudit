import logging

from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.authentication import AuditAuthentication

from audit.models import AuditJob
from audit.serializers import AuditJobSerializer, StartAuditSerializer
from audit.tasks import clone_repo
from github_app.github import InstallationNotFoundError, repo_is_accessible
from github_app.models import Installation


logger = logging.getLogger(__name__)

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
            logger.exception("Unexpected error checking repo access for %s", repo_full_name)
            return Response(
                {"error": "Failed to validate repository access. Please try again."},
                status=503,
            )

        if not repo_allowed:
            return Response(
                {"error": "Repository is not accessible to this GitHub installation."},
                status=400,
            )

        # Only staff may keep sources; non-staff/anonymous always get False.
        # Staff defaults to False; must explicitly opt in.
        keep_sources = request.user.is_staff and serializer.validated_data.get(
            "keep_sources", False
        )

        audit_job = AuditJob.objects.create(
            installation=installation,
            repo_full_name=repo_full_name,
            email=email,
            state=AuditJob.State.PENDING,
            keep_sources=keep_sources,
        )

        clone_repo.delay(audit_job.pk)

        output_serializer = AuditJobSerializer(audit_job)
        return Response(output_serializer.data, status=201)


class MeView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"is_staff": request.user.is_staff})
