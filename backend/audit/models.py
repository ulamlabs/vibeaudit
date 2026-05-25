from django.db import models


class AuditJob(models.Model):
    """Model representing an audit job for a GitHub repository."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("cloning", "Cloning"),
        ("awaiting_approval", "Awaiting Approval"),
        ("running", "Running"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

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
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        help_text="Current status of the audit job",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    report = models.TextField(
        blank=True, help_text="Audit report content (empty until job completes)"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AuditJob #{self.pk} ({self.repo_full_name} - {self.status})"
