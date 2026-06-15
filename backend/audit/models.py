import shutil
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.template import Template, TemplateSyntaxError
from django.template.base import VariableNode
from django.utils import timezone


class AuditJob(models.Model):
    """Model representing an audit job for a GitHub repository."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        CLONING = "cloning", "Cloning"
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting Approval"
        READY = "ready", "Ready"
        CLOSED = "closed", "Closed"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"

    # States that hold a live clone / unfinished work — block installation deletion.
    ACTIVE_STATES = [State.PENDING, State.CLONING, State.AWAITING_APPROVAL, State.READY]

    VALID_TRANSITIONS: dict[str, list[str]] = {
        State.PENDING: [State.CLONING, State.CLOSED],
        State.CLONING: [State.AWAITING_APPROVAL, State.FAILED],
        State.AWAITING_APPROVAL: [State.READY, State.REJECTED, State.CLOSED],
        State.READY: [State.CLOSED],
        State.REJECTED: [State.CLOSED],
        State.FAILED: [State.CLOSED],
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
    keep_sources = models.BooleanField(
        default=False,
        help_text="Keep the cloned sources after a run so the job can be re-run. "
        "Defaults False (external jobs are cleaned up after one run); only staff "
        "submitters may opt in to keeping sources.",
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

    def default_suite(self):
        suite = AuditSuite.objects.filter(is_default=True).first()
        if suite is None:
            raise ValueError("No default AuditSuite configured.")
        return suite

    @property
    def is_runnable(self) -> bool:
        # Only a READY job still has its clone on disk.
        return self.state == self.State.READY

    @property
    def is_running(self) -> bool:
        # True if any run for this job is currently executing.
        return self.runs.filter(status=AuditRun.Status.RUNNING).exists()

    def start_run(self, suite):
        """Create and enqueue a run for a READY job."""
        if not self.is_runnable:
            raise ValueError(f"Cannot start a run while job is {self.state!r}")
        run = AuditRun.objects.create(job=self, suite=suite)
        run.enqueue()
        return run

    def approve(self) -> None:
        # Resolve suite before transitioning so missing default doesn't strand job
        suite = self.default_suite()
        self.transition_to(self.State.READY)
        self.start_run(suite)

    def reject(self) -> None:
        self.transition_to(self.State.REJECTED)
        self.delete_clone()

    def cleanup(self) -> None:
        self.transition_to(self.State.CLOSED)
        self.delete_clone()

    def __str__(self):
        return f"AuditJob #{self.pk} ({self.repo_full_name} - {self.state})"


# Variables available in email_subject and email_html_body Django templates.
# BACKWARD COMPATIBILITY: variables in this set must never be removed or renamed.
# Existing custom templates stored in the database rely on them.
# Adding new variables is always safe; removing or renaming is a breaking change.
ALLOWED_EMAIL_TEMPLATE_VARS = frozenset(
    {
        "repo_name",   # job.repo_full_name
        "summary",     # run.summary
        "run_status",  # run.status  (e.g. 'completed' / 'failed')
        "suite_name",  # suite.name
        "pdf_attached",  # bool — True when PDF was successfully attached
        "site_url",    # settings.SITE_URL or blank
    }
)


class AuditSuite(models.Model):
    """A named, editable collection of specialist agents."""

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(
        default=False, help_text="Suite used for the automatic run created on approval."
    )
    orchestrator_prompt = models.TextField(
        blank=True,
        help_text="Optional override of the orchestrator system prompt; blank uses the code default.",
    )
    report_template = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Custom HTML report template for PDF generation. "
            "Leave blank to use the REPORT_TEMPLATE_PATH env var or the bundled default."
        ),
    )
    model = models.CharField(
        max_length=100,
        help_text="Model name (e.g. 'claude-opus-4-7'). Combined with AI_MODEL_PROVIDER at run time.",
    )
    email_subject = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=(
            "Django template syntax for the email subject line. "
            "Available vars: job, suite, run. "
            "Blank uses the built-in default."
        ),
    )
    email_html_body = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Full HTML email body with Django template syntax. "
            "Available vars: job, suite, run, pdf_attached, site_url. "
            "Blank uses the compiled MJML template from source."
        ),
    )
    task_soft_time_limit_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional Celery soft time limit override in seconds for runs started with this suite.",
    )
    task_time_limit_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional Celery hard time limit override in seconds for runs started with this suite.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def clean(self):
        errors = {}
        for field_name in ("email_subject", "email_html_body"):
            value = getattr(self, field_name)
            if not value:
                continue
            try:
                tpl = Template(value)
            except TemplateSyntaxError as exc:
                errors[field_name] = ValidationError(
                    f"Invalid Django template syntax: {exc}"
                )
                continue
            top_level_vars = set()
            for node in tpl.nodelist.get_nodes_by_type(VariableNode):
                try:
                    raw = node.filter_expression.var.var
                except AttributeError:
                    continue
                top_level_vars.add(raw.split(".")[0])
            unknown = top_level_vars - ALLOWED_EMAIL_TEMPLATE_VARS
            if unknown:
                supported = ", ".join(sorted(ALLOWED_EMAIL_TEMPLATE_VARS))
                errors[field_name] = ValidationError(
                    f"Unsupported template variable(s): {', '.join(sorted(unknown))}. "
                    f"Supported variables: {supported}."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        touches_default = update_fields is None or "is_default" in update_fields
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_default and touches_default:
                AuditSuite.objects.exclude(pk=self.pk).filter(is_default=True).update(
                    is_default=False
                )

    def resolve_task_time_limits(self) -> tuple[int, int]:
        soft_default = max(1, settings.AUDIT_TASK_SOFT_TIME_LIMIT_SECONDS)
        hard_default = max(
            soft_default + 1,
            settings.AUDIT_TASK_TIME_LIMIT_SECONDS,
        )

        soft_limit = self.task_soft_time_limit_seconds or soft_default
        hard_limit = self.task_time_limit_seconds or hard_default

        if hard_limit <= soft_limit:
            hard_limit = soft_limit + 1

        return soft_limit, hard_limit

    def __str__(self):
        return self.name


class AuditAgent(models.Model):
    """A specialist agent owned by a suite (mirrors ai.agents.AgentDefinition)."""

    suite = models.ForeignKey(
        AuditSuite, related_name="agents", on_delete=models.CASCADE
    )
    agent_id = models.SlugField(
        max_length=80, help_text="Subagent identifier passed to the orchestrator."
    )
    name = models.CharField(max_length=120)
    description = models.TextField(help_text="Delegation blurb the orchestrator sees.")
    prompt = models.TextField(help_text="Specialist focus instructions.")
    position = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [("suite", "agent_id")]
        ordering = ["position", "id"]

    def __str__(self):
        return f"suite {self.suite_id} / {self.agent_id}"


class AuditRun(models.Model):
    """One execution of one suite against a job's clone."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    job = models.ForeignKey(AuditJob, related_name="runs", on_delete=models.CASCADE)
    suite = models.ForeignKey(AuditSuite, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    summary = models.TextField(blank=True)
    markdown = models.TextField(
        blank=True, help_text="Report body (no top-level title)."
    )
    error = models.TextField(blank=True)
    celery_task_id = models.CharField(
        max_length=36, blank=True, help_text="Celery task ID for tracking/revoking."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def terminate(self, reason: str) -> None:
        """Mark this run as failed with a reason and record finished_at."""
        self.status = AuditRun.Status.FAILED
        self.error = reason
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error", "finished_at"])

    def enqueue(self):
        """Queue this run and persist the created Celery task ID."""
        from audit.tasks import execute_audit_run

        soft_time_limit, time_limit = self.suite.resolve_task_time_limits()
        task = execute_audit_run.apply_async(
            args=[self.pk],
            soft_time_limit=soft_time_limit,
            time_limit=time_limit,
        )
        self.celery_task_id = task.id or ""
        self.save(update_fields=["celery_task_id"])
        return task

    def __str__(self):
        return f"Run #{self.pk} (job #{self.job_id}, {self.suite_id}, {self.status})"


class AgentRunOutput(models.Model):
    """Raw markdown a specialist subagent returned during a run (for debugging)."""

    run = models.ForeignKey(
        AuditRun, related_name="agent_outputs", on_delete=models.CASCADE
    )
    agent_id = models.CharField(max_length=80)
    output = models.TextField()
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.agent_id} (run #{self.run_id})"
