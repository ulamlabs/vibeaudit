import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.authentication import AuditAuthentication

from audit.models import AuditJob
from audit.serializers import AuditJobSerializer, StartAuditSerializer
from audit.tasks import clone_repo
from github_app.github import InstallationNotFoundError, repo_is_accessible
from github_app.permissions import (
    ActiveInstallationPermission,
    mark_installation_gone,
)


logger = logging.getLogger(__name__)


class StartAuditView(APIView):
    """
    Start a new audit job for a repository.
    Requires installation_id in session.
    """

    authentication_classes = [AuditAuthentication]
    permission_classes = [ActiveInstallationPermission]
    # JSON only: an HTML form can't produce application/json, so cross-site
    # form POSTs (which CORS does not block) are rejected even though the
    # anonymous funnel has no CSRF token. Cross-origin fetch with a JSON body
    # triggers a CORS preflight and is gated by the allowlist.
    parser_classes = [JSONParser]

    def post(self, request):
        installation = request.installation

        # Validate request body
        serializer = StartAuditSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        # Create the audit job
        repo_full_name = serializer.validated_data["repo_full_name"]
        email = serializer.validated_data["email"]

        try:
            repo_allowed = repo_is_accessible(
                installation.installation_id, repo_full_name
            )
        except InstallationNotFoundError:
            mark_installation_gone(installation)
        except Exception:
            logger.exception(
                "Unexpected error checking repo access for %s", repo_full_name
            )
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

        # Publish after commit so a fast worker can't miss the row. Time limits
        # bound a hung clone: the soft limit raises inside the task (normal
        # failure path — staff email, FAILED state); the hard limit is the
        # backstop Celery kill that AuditJob.is_overdue is calibrated against.
        transaction.on_commit(
            lambda: clone_repo.apply_async(
                args=[audit_job.pk],
                soft_time_limit=settings.AUDIT_TASK_SOFT_TIME_LIMIT_SECONDS,
                time_limit=settings.AUDIT_TASK_TIME_LIMIT_SECONDS,
            )
        )

        job_ids = request.session.get("audit_job_ids", [])
        job_ids.append(audit_job.pk)
        request.session["audit_job_ids"] = job_ids
        request.session.save()

        output_serializer = AuditJobSerializer(audit_job)
        return Response(output_serializer.data, status=201)


class AuditSessionView(APIView):
    """
    GET /api/audit/session — counts of audits the caller has submitted.

    Authenticated users are counted by installation ownership, so their totals
    survive a new session or device. Anonymous funnel visitors are counted from
    the job ids recorded on their Django session, which stays accurate after the
    installation is auto-deleted post-clone (when /api/github/installations 404s).
    Consumed by the ulam.io website funnel (getAuditSession).
    """

    authentication_classes = [AuditAuthentication]

    def get(self, request):
        if request.user.is_authenticated:
            jobs = AuditJob.objects.filter(installation__owner=request.user)
        else:
            job_ids = request.session.get("audit_job_ids", [])
            jobs = AuditJob.objects.filter(pk__in=job_ids)
        # FAILED jobs count toward submitted too; harmless since failure keeps the
        # install live and the funnel resumes at the picker (no note shown there).
        counts = jobs.aggregate(
            submitted_count=Count("pk"),
            active_count=Count("pk", filter=Q(state__in=AuditJob.ACTIVE_STATES)),
        )
        return Response(counts)


class MeView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"is_staff": request.user.is_staff})
