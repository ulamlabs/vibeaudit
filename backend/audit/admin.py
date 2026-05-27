from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from unfold.admin import ModelAdmin
from unfold.decorators import action

from audit.models import AuditJob
from audit.services import approve_job, reject_job


@admin.register(AuditJob)
class AuditJobAdmin(ModelAdmin):
    list_display = [
        "id",
        "repo_full_name",
        "email",
        "state",
        "installation",
        "created_at",
    ]
    list_filter = ["state"]
    search_fields = ["repo_full_name", "email"]
    readonly_fields = [
        "installation",
        "repo_full_name",
        "email",
        "created_at",
    ]
    fieldsets = [
        (
            "Job",
            {
                "fields": [
                    "repo_full_name",
                    "email",
                    "state",
                    "created_at",
                ]
            },
        ),
        (
            "GitHub Installation",
            {
                "fields": [
                    "installation",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Report",
            {
                "fields": ["report"],
            },
        ),
    ]
    ordering = ["-created_at"]
    actions = ["bulk_reject"]
    actions_detail = ["approve_job", "reject_job"]

    @action(description="Approve", url_path="approve")
    def approve_job(self, request, object_id):
        job = AuditJob.objects.get(pk=object_id)
        try:
            approve_job(job)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(request, f"Job #{job.pk} approved and queued.")
        return self._redirect_to_change(request, object_id)

    @action(description="Reject", url_path="reject")
    def reject_job(self, request, object_id):
        job = AuditJob.objects.get(pk=object_id)
        try:
            reject_job(job)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(request, f"Job #{job.pk} rejected.", messages.WARNING)
        return self._redirect_to_change(request, object_id)

    @admin.action(description="Reject selected jobs")
    def bulk_reject(self, request, queryset):
        rejected = skipped = 0
        for job in queryset:
            try:
                reject_job(job)
                rejected += 1
            except ValueError:
                skipped += 1
        if rejected:
            self.message_user(request, f"{rejected} job(s) rejected.")
        if skipped:
            self.message_user(
                request,
                f"{skipped} job(s) skipped (not in a rejectable state).",
                messages.WARNING,
            )

    def _redirect_to_change(self, request, object_id):
        return redirect(reverse("admin:audit_auditjob_change", args=[object_id]))
