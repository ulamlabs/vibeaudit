from django.conf import settings
from django.db import models
from django.utils import timezone

from github_app.github import InstallationNotFoundError, delete_installation


class Installation(models.Model):
    """Permanent record of a GitHub App installation."""

    ACCOUNT_TYPE_CHOICES = [
        ("User", "User"),
        ("Organization", "Organization"),
    ]

    installation_id = models.BigIntegerField(
        unique=True,
        help_text="GitHub's installation ID",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="installations",
        help_text=(
            "Authenticated user who connected this installation. Their "
            "installations (and audits) resolve by ownership across sessions "
            "and devices; anonymous funnel installs have no owner and are "
            "tracked by session instead."
        ),
    )
    account_login = models.CharField(
        max_length=255,
        help_text="GitHub username or organisation name",
    )
    account_type = models.CharField(
        max_length=50,
        choices=ACCOUNT_TYPE_CHOICES,
        help_text="'User' or 'Organization'",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    remote_deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when GitHub confirms the installation no longer exists",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Installation #{self.installation_id} ({self.account_login})"

    def mark_remote_deleted(self) -> None:
        """Mark the installation as no longer existing on GitHub."""
        self.remote_deleted_at = timezone.now()
        self.save(update_fields=["remote_deleted_at"])

    def reactivate(self, *, account_login: str, account_type: str) -> None:
        """Refresh local metadata for an active installation seen again on GitHub."""
        self.account_login = account_login
        self.account_type = account_type
        self.remote_deleted_at = None
        self.save(update_fields=["account_login", "account_type", "remote_deleted_at"])

    def has_active_audit_jobs(self) -> bool:
        from audit.models import AuditJob  # local import to avoid circular dependency

        return self.audit_jobs.filter(state__in=AuditJob.ACTIVE_STATES).exists()

    def uninstall(self) -> None:
        """Delete the installation from GitHub and mark it as remote-deleted locally."""
        try:
            delete_installation(self.installation_id)
        except InstallationNotFoundError:
            pass
        self.mark_remote_deleted()
