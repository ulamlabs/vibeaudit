import logging

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from unfold.admin import ModelAdmin
from unfold.decorators import action

from github_app.github import InstallationNotFoundError, check_installation_active
from github_app.models import Installation

logger = logging.getLogger(__name__)


@admin.action(description="Remote delete selected installations (uninstalls from GitHub)")
def remote_delete_bulk(modeladmin, request, queryset):
    for installation in queryset.filter(remote_deleted_at__isnull=True):
        installation.uninstall()


@admin.action(description="Verify with GitHub and mark missing installations as remote-deleted")
def verify_with_github_bulk(modeladmin, request, queryset):
    for installation in queryset.filter(remote_deleted_at__isnull=True):
        try:
            check_installation_active(installation.installation_id)
        except InstallationNotFoundError:
            installation.mark_remote_deleted()
        except Exception:
            logger.exception("Failed to verify installation %s with GitHub", installation.installation_id)


@admin.register(Installation)
class InstallationAdmin(ModelAdmin):
    list_display = [
        "installation_id",
        "account_login",
        "account_type",
        "created_at",
        "remote_deleted_at",
    ]
    list_filter = ["account_type"]
    search_fields = ["account_login", "installation_id"]
    readonly_fields = ["installation_id", "account_login", "account_type", "created_at"]
    actions = [remote_delete_bulk, verify_with_github_bulk]
    actions_detail = ["remote_delete_detail", "verify_with_github_detail"]
    ordering = ["-created_at"]

    @action(description="Remote Delete", url_path="remote-delete")
    def remote_delete_detail(self, request, object_id):
        installation = self.get_object(request, object_id)
        if not installation:
            return redirect(reverse("admin:github_app_installation_change", args=[object_id]))
        if installation.remote_deleted_at:
            self.message_user(request, "Installation is already remote-deleted.", messages.WARNING)
        else:
            installation.uninstall()
            self.message_user(request, "Installation uninstalled from GitHub and marked as remote-deleted.", messages.SUCCESS)
        return redirect(reverse("admin:github_app_installation_change", args=[object_id]))

    @action(description="Check on GitHub", url_path="check-github")
    def verify_with_github_detail(self, request, object_id):
        installation = self.get_object(request, object_id)
        if not installation:
            return redirect(reverse("admin:github_app_installation_change", args=[object_id]))
        if installation.remote_deleted_at:
            self.message_user(request, "Installation is already marked as remote-deleted locally.", messages.WARNING)
        else:
            try:
                check_installation_active(installation.installation_id)
                self.message_user(request, "Installation is still active on GitHub.", messages.SUCCESS)
            except InstallationNotFoundError:
                installation.mark_remote_deleted()
                self.message_user(request, "Installation no longer exists on GitHub. Marked as remote-deleted.", messages.WARNING)
            except Exception as e:
                self.message_user(request, f"Could not verify with GitHub: {e}", messages.ERROR)
        return redirect(reverse("admin:github_app_installation_change", args=[object_id]))
