from django.db import models


class AuditJob(models.Model):
    """Model representing an audit job for a GitHub repository."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        CLONING = "cloning", "Cloning"
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting Approval"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"
        CANCELED = "canceled", "Canceled"

    installation = models.ForeignKey(
        "github_app.Installation",
        on_delete=models.PROTECT,
        related_name="audit_jobs",
        help_text="GitHub App installation that initiated this audit",
    )
    repo_full_name = models.CharField(
        max_length=255, help_text="Full repository name (owner/repo)"
    )
    email = models.EmailField(help_text="Email address to send the report to")
    state = models.CharField(
        max_length=32,
        choices=State.choices,
        default=State.PENDING,
        help_text="Current state of the audit job",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    report = models.TextField(
        blank=True, help_text="Audit report content (empty until job completes)"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AuditJob #{self.pk} ({self.repo_full_name} - {self.state})"
