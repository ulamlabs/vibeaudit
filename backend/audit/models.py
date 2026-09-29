from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.template import Template, TemplateSyntaxError
from django.template.base import VariableNode
from django.utils import timezone

# Slack on top of a task's hard time limit before a stuck object counts as
# overdue (the hard limit SIGKILLs the worker, skipping failure handling).
OVERDUE_GRACE_SECONDS = 120

# A send is one PDF render plus one SMTP round trip; a report still `sending`
# well past that lost its worker before it could roll back or finish.
SEND_STRANDED_SECONDS = 900


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
        help_text="Never auto-delete the cloned sources. Off (default) means the "
        "clone is removed once every report on this job has been approved and "
        "sent; only staff submitters may opt in to keeping sources.",
    )

    class Meta:
        ordering = ["-created_at"]

    def transition_to(self, new_state: str) -> None:
        allowed = self.VALID_TRANSITIONS.get(self.state, [])
        if new_state not in allowed:
            raise ValueError(f"Cannot transition from {self.state!r} to {new_state!r}")
        # Compare-and-set: the UPDATE only matches if the DB still holds the
        # state we validated against, so two concurrent transitions (e.g. a
        # double-clicked Approve) cannot both pass the guard.
        updated = AuditJob.objects.filter(pk=self.pk, state=self.state).update(
            state=new_state
        )
        if not updated:
            self.refresh_from_db(fields=["state"])
            raise ValueError(
                f"Cannot transition to {new_state!r}: job concurrently "
                f"moved to {self.state!r}"
            )
        self.state = new_state

    @staticmethod
    def job_dir_for(job_id: int) -> Path:
        """Where a job's clone lives, by id — usable once the row is gone."""
        return Path(settings.REPOS_DIR) / str(job_id)

    @property
    def job_dir(self) -> Path:
        return self.job_dir_for(self.pk)

    @property
    def clone_path(self) -> Path:
        return self.job_dir / self.repo_full_name

    def delete_clone(self) -> None:
        """Hand the rmtree to a worker instead of running it here.

        Only the worker pod mounts the repos volume (it is ReadWriteOnce, so the
        web pod cannot mount it too). An rmtree issued from the admin therefore
        deleted nothing while every caller here reported success.
        """
        from audit.tasks import delete_job_dir

        job_id = self.pk
        transaction.on_commit(lambda: delete_job_dir.delay(job_id))

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
    def is_overdue(self) -> bool:
        """
        Lazily detect a clone worker that died without running its failure
        handling (a hard-time-limit SIGKILL or crash skips clone_repo's except
        block): CLONING past the task's hard time limit plus grace. Checked on
        read — no beat schedule needed. Remedy: the admin "Mark failed" action.
        """
        if self.state != self.State.CLONING:
            return False
        deadline = self.created_at + timedelta(
            seconds=settings.AUDIT_TASK_TIME_LIMIT_SECONDS + OVERDUE_GRACE_SECONDS
        )
        return timezone.now() > deadline

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

    @staticmethod
    def outstanding_runs_q() -> models.Q:
        """The condition for 'this run still needs the clone or a human decision'.

        Single source of truth shared by has_outstanding_runs and the admin's
        annotated queryset — maybe_cleanup_sources decides whether to delete a
        customer's cloned sources based on this, so the two call sites must
        never drift into independently-written copies of the condition.
        """
        return models.Q(
            status__in=[AuditRun.Status.PENDING, AuditRun.Status.RUNNING]
        ) | models.Q(
            report_state__in=[
                AuditRun.ReportState.AWAITING_APPROVAL,
                AuditRun.ReportState.APPROVED,
                AuditRun.ReportState.SENDING,
            ]
        )

    @property
    def has_outstanding_runs(self) -> bool:
        """True while some run still needs the clone or a human decision."""
        return self.runs.filter(self.outstanding_runs_q()).exists()

    @classmethod
    def maybe_cleanup_sources(cls, job_id: int) -> bool:
        """
        Close the job and drop its clone once nothing needs it any more.
        Locks the job row: two reports approved at the same instant would
        otherwise each read the other as outstanding and both skip cleanup,
        stranding the clone on disk forever.
        """
        with transaction.atomic():
            job = cls.objects.select_for_update().get(pk=job_id)
            if job.keep_sources or not job.is_runnable:
                return False
            if job.has_outstanding_runs:
                return False
            job.cleanup()
            return True

    def __str__(self):
        return f"AuditJob #{self.pk} ({self.repo_full_name} - {self.state})"


# Variables available in email_html_body Django templates.
# BACKWARD COMPATIBILITY: variables in this set must never be removed or renamed.
# Existing custom templates stored in the database rely on them.
# Adding new variables is always safe; removing or renaming is a breaking change.
ALLOWED_EMAIL_TEMPLATE_VARS = frozenset(
    {
        "repo_name",  # job.repo_full_name
        "summary",  # run.summary
        "run_status",  # run.status  (e.g. 'completed' / 'failed')
        "suite_name",  # suite.name
        "pdf_attached",  # bool — True when PDF was successfully attached
        "site_url",  # settings.SITE_URL or blank
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
        help_text=(
            "Report/structure instructions given to the orchestrator as the user prompt — "
            "what to do with the findings and how to structure the report. "
            "Blank uses the built-in default. "
            "The hard requirements, subagent-calling mechanics, and output form are "
            "framework-owned and not editable here."
        ),
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
        help_text="Model name from AVAILABLE_AI_MODELS. Combined with AI_MODEL_PROVIDER at run time.",
    )
    email_html_body = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Full HTML email body with Django template syntax. "
            "Available vars: repo_name, summary, run_status, suite_name, pdf_attached, site_url. "
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
    max_run_cost_usd = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Optional override for the cumulative estimated dollar budget of a whole "
            "run (orchestrator + subagents). Blank/0 falls back to AI_MAX_RUN_COST_USD."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    agents: models.ManyToManyField[AuditAgent, AuditAgent] = models.ManyToManyField(
        "AuditAgent",
        blank=True,
        related_name="suites",
    )

    class Meta:
        ordering = ["name"]

    def clean(self):
        if not self.email_html_body:
            return
        try:
            tpl = Template(self.email_html_body)
        except TemplateSyntaxError as exc:
            raise ValidationError(
                {"email_html_body": f"Invalid Django template syntax: {exc}"}
            )
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
            raise ValidationError(
                {
                    "email_html_body": (
                        f"Unsupported template variable(s): {', '.join(sorted(unknown))}. "
                        f"Supported variables: {supported}."
                    )
                }
            )

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

    def resolve_ai_run_limits(self) -> tuple[int, float]:
        """Resolve per-run agent guardrails. Returns (recursion_limit, max_run_cost_usd).

        The recursion limit is global (settings.AI_RECURSION_LIMIT). Only the cost cap
        is overridable per suite; a suite value of 0 falls back to
        settings.AI_MAX_RUN_COST_USD. A resolved 0 disables that guard downstream.
        """
        max_run_cost = self.max_run_cost_usd or settings.AI_MAX_RUN_COST_USD
        return settings.AI_RECURSION_LIMIT, float(max_run_cost)

    def __str__(self):
        return self.name


class AuditAgent(models.Model):
    """A specialist agent in the shared library (used by one or more suites)."""

    agent_id = models.SlugField(
        max_length=80,
        unique=True,
        help_text="Subagent identifier passed to the orchestrator.",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(help_text="Delegation blurb the orchestrator sees.")
    prompt = models.TextField(help_text="Specialist focus instructions.")

    class Meta:
        ordering = ["agent_id"]

    def __str__(self):
        return self.name


class AuditRun(models.Model):
    """One execution of one suite against a job's clone."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class ReportState(models.TextChoices):
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting Approval"
        APPROVED = "approved", "Approved"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        REJECTED = "rejected", "Rejected"

    # Blank (the default) means "no report stage" — a run that has not completed.
    # `sending` is claimed atomically before the send so two workers cannot both
    # deliver; it rolls back to `approved` when the send fails, which is what
    # makes `approved` mean "retry me" and keeps the Resend action honest.
    REPORT_VALID_TRANSITIONS: dict[str, list[str]] = {
        ReportState.AWAITING_APPROVAL: [ReportState.APPROVED, ReportState.REJECTED],
        ReportState.APPROVED: [ReportState.SENDING],
        ReportState.SENDING: [ReportState.SENT, ReportState.APPROVED],
    }

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
    cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            "Actual cumulative USD cost of this run (orchestrator + subagents), "
            "measured across model calls. Null when not tracked."
        ),
    )
    celery_task_id = models.CharField(
        max_length=36, blank=True, help_text="Celery task ID for tracking/revoking."
    )
    report_state = models.CharField(
        max_length=32,
        choices=ReportState.choices,
        blank=True,
        default="",
        help_text=(
            "Delivery stage of this run's report. Blank until the run completes; "
            "a completed report waits at 'awaiting_approval' until a staff member "
            "approves or rejects it."
        ),
    )
    sending_since = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when a worker claims this report for sending; cleared on any "
        "transition out of 'sending'. Used to spot a worker that died mid-send.",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set on a fresh approval (awaiting_approval -> approved); cleared on "
        "transitions out of the approval stage, except a failed-send rollback, which "
        "preserves it. Used to spot a queued send whose message was lost.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_overdue(self) -> bool:
        """
        Lazily detect a run whose worker died without running the task's
        failure handling (hard-time-limit SIGKILL or worker crash skips both
        except and finally): RUNNING past the suite's hard time limit plus
        grace. Checked on read — no beat schedule needed. Remedy: the admin
        "Terminate execution" action.
        """
        if self.status != AuditRun.Status.RUNNING or not self.started_at:
            return False
        _, hard_limit = self.suite.resolve_task_time_limits()
        deadline = self.started_at + timedelta(
            seconds=hard_limit + OVERDUE_GRACE_SECONDS
        )
        return timezone.now() > deadline

    def terminate(self, reason: str, *, cost_usd=None) -> bool:
        """
        Mark a PENDING/RUNNING run as failed with a reason (optionally recording
        the measured cost). Compare-and-set: a run that already finished is left
        untouched (returns False), so an admin terminate racing task completion
        cannot flip COMPLETED to FAILED.
        """
        fields = {
            "status": AuditRun.Status.FAILED,
            "error": reason,
            "finished_at": timezone.now(),
        }
        if cost_usd is not None:
            fields["cost_usd"] = cost_usd
        updated = AuditRun.objects.filter(
            pk=self.pk,
            status__in=[AuditRun.Status.PENDING, AuditRun.Status.RUNNING],
        ).update(**fields)
        if updated:
            self.refresh_from_db(fields=list(fields.keys()))
        return bool(updated)

    def transition_report_to(self, new_state: str) -> None:
        allowed = self.REPORT_VALID_TRANSITIONS.get(self.report_state, [])
        if new_state not in allowed:
            raise ValueError(
                f"Cannot transition report from {self.report_state!r} to {new_state!r}"
            )
        # Compare-and-set, like AuditJob.transition_to: a double-clicked Approve
        # cannot pass the guard twice.
        # Only send_approved_report's claim sets sending_since; every transition
        # out of `sending` clears it EXCEPT the failed-send rollback (sending ->
        # approved), which preserves it — that is the exact signal send_failed
        # reads to flag "a send was attempted and failed" with no time window.
        is_failed_send_rollback = (
            self.report_state == AuditRun.ReportState.SENDING
            and new_state == AuditRun.ReportState.APPROVED
        )
        # A fresh approval stamps approved_at; every other transition clears it
        # (approved_at only means something for a report currently sitting at
        # 'approved') EXCEPT the failed-send rollback, which preserves it like
        # sending_since — it's already flagged via send_failed, so it must not
        # also silently lose when it was first approved.
        is_fresh_approval = (
            self.report_state == AuditRun.ReportState.AWAITING_APPROVAL
            and new_state == AuditRun.ReportState.APPROVED
        )
        update_fields: dict[str, str | datetime | None] = {"report_state": new_state}
        if not is_failed_send_rollback:
            update_fields["sending_since"] = None
        if is_fresh_approval:
            update_fields["approved_at"] = timezone.now()
        elif not is_failed_send_rollback:
            update_fields["approved_at"] = None
        updated = AuditRun.objects.filter(
            pk=self.pk, report_state=self.report_state
        ).update(**update_fields)
        if not updated:
            self.refresh_from_db(fields=["report_state"])
            raise ValueError(
                f"Cannot transition report to {new_state!r}: run concurrently "
                f"moved to {self.report_state!r}"
            )
        self.report_state = new_state
        if not is_failed_send_rollback:
            self.sending_since = None
        if "approved_at" in update_fields:
            self.approved_at = update_fields["approved_at"]

    @property
    def send_is_stranded(self) -> bool:
        """
        Lazily detect a worker that died mid-send: `sending` past a generous send
        window. Checked on read — no beat schedule needed, mirroring is_overdue.
        Remedy: the admin "Reset to approved" action.
        """
        if self.report_state != AuditRun.ReportState.SENDING or not self.sending_since:
            return False
        return timezone.now() > self.sending_since + timedelta(
            seconds=SEND_STRANDED_SECONDS
        )

    @property
    def send_failed(self) -> bool:
        """
        `approved` with a non-null `sending_since` means exactly one thing: a
        send was attempted and rolled back on failure. A fresh approval never
        sets `sending_since`, so this can't false-positive on a normal queue.
        """
        return (
            self.report_state == AuditRun.ReportState.APPROVED
            and self.sending_since is not None
        )

    @property
    def send_never_claimed(self) -> bool:
        """
        Lazily detect an approval whose queue message was lost (broker restart,
        etc.): `approved`, never claimed for sending (sending_since still null),
        past a generous window since approval. Checked on read — no beat
        schedule needed, mirroring is_overdue and send_is_stranded. Reuses
        SEND_STRANDED_SECONDS rather than a second tunable — both windows mean
        "this long stuck partway through a send is anomalous". Remedy: the
        admin "Resend report" action.
        """
        if (
            self.report_state != AuditRun.ReportState.APPROVED
            or self.sending_since is not None
            or not self.approved_at
        ):
            return False
        return timezone.now() > self.approved_at + timedelta(
            seconds=SEND_STRANDED_SECONDS
        )

    def enqueue_send(self):
        """Queue the approved report for delivery once the current transaction commits."""
        from audit.tasks import send_approved_report

        transaction.on_commit(lambda: send_approved_report.delay(self.pk))

    def enqueue(self):
        """
        Queue this run once the current transaction commits (immediately under
        autocommit). Publishing pre-commit lets a fast worker look up the run
        before its row is visible — the Django admin, for one, saves inside
        transaction.atomic — and the resulting DoesNotExist permanently
        strands the run in PENDING.
        """
        from audit.tasks import execute_audit_run

        soft_time_limit, time_limit = self.suite.resolve_task_time_limits()

        def publish():
            task = execute_audit_run.apply_async(
                args=[self.pk],
                soft_time_limit=soft_time_limit,
                time_limit=time_limit,
            )
            self.celery_task_id = task.id or ""
            # .update: in eager mode the task has already run by now, so a
            # full save would clobber its status/finished_at.
            AuditRun.objects.filter(pk=self.pk).update(
                celery_task_id=self.celery_task_id
            )

        transaction.on_commit(publish)

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
