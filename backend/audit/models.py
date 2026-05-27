import shutil
from pathlib import Path

from django.conf import settings
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

    VALID_TRANSITIONS: dict[str, list[str]] = {
        State.AWAITING_APPROVAL: [State.QUEUED, State.REJECTED],
        State.QUEUED: [State.RUNNING],
        State.RUNNING: [State.COMPLETED, State.FAILED],
    }

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

    def transition_to(self, new_state: str) -> None:
        allowed = self.VALID_TRANSITIONS.get(self.state, [])
        if new_state not in allowed:
            raise ValueError(f"Cannot transition from {self.state!r} to {new_state!r}")
        self.state = new_state
        self.save(update_fields=["state"])

    @property
    def job_dir(self) -> Path:
        return Path(settings.REPOS_DIR) / str(self.pk)

    @property
    def clone_path(self) -> Path:
        return self.job_dir / self.repo_full_name

    def delete_clone(self) -> None:
        shutil.rmtree(self.job_dir, ignore_errors=True)

    def approve(self) -> None:
        self.transition_to(self.State.QUEUED)

    def reject(self) -> None:
        self.transition_to(self.State.REJECTED)

    def __str__(self):
        return f"AuditJob #{self.pk} ({self.repo_full_name} - {self.state})"
