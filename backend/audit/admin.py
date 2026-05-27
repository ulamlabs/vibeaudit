from django.contrib import admin
from unfold.admin import ModelAdmin

from audit.models import AuditJob


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
