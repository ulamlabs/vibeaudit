from django import forms
from celery import current_app
from django.contrib import admin, messages
from django.conf import settings as django_settings
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.db.models import Exists, OuterRef
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from audit.models import AgentRunOutput, AuditAgent, AuditJob, AuditRun, AuditSuite
from audit.pdf import render_pdf
from audit.rendering import render_markdown_safe


def _rendered_markdown(md: str):
    # .md-preview lets the scoped stylesheet restyle what Unfold's Tailwind reset strips.
    if not md:
        return "—"
    return mark_safe(f'<div class="md-preview">{render_markdown_safe(md)}</div>')


def _collapsed_raw(text: str):
    if not text:
        return "—"
    # format_html escapes `text`, so the raw markdown renders literally.
    return format_html(
        '<details><summary style="cursor: pointer">Show raw markdown</summary>'
        '<pre style="white-space: pre-wrap; word-break: break-word">{}</pre></details>',
        text,
    )


class AuditRunForm(forms.ModelForm):
    class Meta:
        model = AuditRun
        fields = ["job", "suite"]

    def clean(self):
        cleaned = super().clean()
        # Gate creation only; an existing run stays viewable after its job closes.
        if self.instance.pk is None:
            job = cleaned.get("job")
            if job is not None and not job.is_runnable:
                raise forms.ValidationError(
                    f"Cannot start a run for job #{job.pk}: it is '{job.state}', not "
                    f"'{AuditJob.State.READY}'. A run needs a READY job with cloned "
                    "sources present."
                )
        return cleaned


@admin.register(AuditAgent)
class AuditAgentAdmin(ModelAdmin):
    list_display = ["agent_id", "name", "suite_list"]
    search_fields = ["agent_id", "name", "description"]

    @admin.display(description="Used by suites")
    def suite_list(self, obj):
        names = obj.suites.values_list("name", flat=True)
        return ", ".join(names) if names else "—"


class AuditSuiteAdminForm(forms.ModelForm):
    class Meta:
        model = AuditSuite
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        available = django_settings.AVAILABLE_AI_MODELS
        if available:
            self.fields["model"].widget = forms.Select(
                choices=[(m, m) for m in available]
            )
        # If AVAILABLE_AI_MODELS is empty, the default text input remains.


@admin.register(AuditSuite)
class AuditSuiteAdmin(ModelAdmin):
    form = AuditSuiteAdminForm
    list_display = ["name", "model", "is_default", "agent_count", "created_at"]
    list_filter = ["is_default"]
    search_fields = ["name", "description"]
    filter_horizontal = ("agents",)

    _EMAIL_HELP = (
        "<strong>Available template variables:</strong> "
        "<code>{{ repo_name }}</code> &mdash; full repository name, "
        "<code>{{ summary }}</code> &mdash; run summary text, "
        "<code>{{ run_status }}</code> &mdash; e.g. <em>completed</em> / <em>failed</em>, "
        "<code>{{ suite_name }}</code> &mdash; suite name, "
        "<code>{{ pdf_attached }}</code> &mdash; bool, "
        "<code>{{ site_url }}</code> &mdash; site URL from settings."
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "email_html_body" in form.base_fields:
            form.base_fields["email_html_body"].widget = forms.Textarea(
                attrs={
                    "rows": 30,
                    "style": "font-family: monospace; font-size: 12px;",
                }
            )
            form.base_fields["email_html_body"].help_text = mark_safe(self._EMAIL_HELP)
        return form

    @admin.display(description="Agents")
    def agent_count(self, obj):
        return obj.agents.count()


class AgentRunOutputInline(TabularInline):
    model = AgentRunOutput
    extra = 0
    can_delete = False
    fields = ["agent_id", "rendered_output", "raw_output"]
    readonly_fields = ["agent_id", "rendered_output", "raw_output"]

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Output")
    def rendered_output(self, obj):
        return _rendered_markdown(obj.output)

    @admin.display(description="Raw")
    def raw_output(self, obj):
        return _collapsed_raw(obj.output)


@admin.register(AuditRun)
class AuditRunAdmin(ModelAdmin):
    form = AuditRunForm
    list_display = [
        "id",
        "job",
        "suite",
        "status",
        "report_state",
        "needs_attention",
        "cost_usd",
        "created_at",
    ]
    list_filter = ["status", "report_state", "suite", "job"]
    search_fields = ["job__repo_full_name"]
    inlines = [AgentRunOutputInline]
    _BASE_READONLY = [
        "status",
        "report_state",
        "summary",
        "report_html",
        "report_raw",
        "error",
        "cost_usd",
        "created_at",
        "started_at",
        "finished_at",
        "celery_task_id",
    ]

    def get_readonly_fields(self, request, obj=None):
        fields = list(self._BASE_READONLY)
        # The report body is editable only while a human is deciding on it.
        # Enforced here, not just in get_fields — hiding a field does not stop a POST.
        if obj is None or obj.report_state != AuditRun.ReportState.AWAITING_APPROVAL:
            fields.append("markdown")
        return fields

    actions_detail = [
        "approve_report",
        "reject_report",
        "resend_report",
        "reset_stranded_send",
        "terminate_run",
        "download_pdf",
    ]

    @action(description="Download PDF", url_path="download-pdf")
    def download_pdf(self, request, object_id):
        run = AuditRun.objects.get(pk=object_id)
        try:
            pdf_bytes = render_pdf(run)
        except Exception as exc:
            self.message_user(request, f"PDF generation failed: {exc}", messages.ERROR)
            return redirect(reverse("admin:audit_auditrun_change", args=[object_id]))
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="report-{run.pk}.pdf"'
        return response

    def get_fields(self, request, obj=None):
        if obj is None:
            return ["job", "suite"]
        fields = ["job", "suite", "status", "report_state", "summary"]
        if obj.report_state == AuditRun.ReportState.AWAITING_APPROVAL:
            fields += ["markdown", "report_html"]
        else:
            fields += ["report_html", "report_raw"]
        return fields + [
            "error",
            "cost_usd",
            "celery_task_id",
            "created_at",
            "started_at",
            "finished_at",
        ]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "markdown" in form.base_fields:
            form.base_fields["markdown"].widget = forms.Textarea(
                attrs={"rows": 30, "style": "font-family: monospace; font-size: 12px;"}
            )
        return form

    @admin.display(description="Needs attention", boolean=True)
    def needs_attention(self, obj):
        # RUNNING past the hard time limit — the worker likely died; use
        # "Terminate execution". Or `sending` past the send window — the worker
        # died mid-send; use "Reset to approved" then Resend. Or `approved`
        # with a rollback's sending_since still set — the send failed and
        # nothing retries it automatically; use "Resend report". Or `approved`
        # with sending_since never set, past the window — the queue message
        # was lost; use "Resend report".
        return (
            obj.is_overdue
            or obj.send_is_stranded
            or obj.send_failed
            or obj.send_never_claimed
        )

    @admin.display(description="Report")
    def report_html(self, obj):
        return _rendered_markdown(obj.markdown)

    @admin.display(description="Report (raw)")
    def report_raw(self, obj):
        return _collapsed_raw(obj.markdown)

    class Media:
        css = {"all": ["audit/md_preview.css"]}

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:  # enqueue on creation only; editing must not re-run
            obj.enqueue()
            self.message_user(request, f"Run #{obj.pk} queued.")

    def _redirect_to_change(self, object_id):
        return redirect(reverse("admin:audit_auditrun_change", args=[object_id]))

    def _run_field(self, object_id, field):
        return (
            AuditRun.objects.filter(pk=object_id).values_list(field, flat=True).first()
        )

    def _awaits_decision(self, object_id) -> bool:
        return (
            self._run_field(object_id, "report_state")
            == AuditRun.ReportState.AWAITING_APPROVAL
        )

    # Unfold renders a detail action only when its has_<name>_permission hook
    # passes. Admin access is already gated on is_staff, so these are not
    # authorization checks — they answer whether the button means anything for
    # this object's current state.
    def has_approve_report_permission(self, request, object_id=None):
        return self._awaits_decision(object_id)

    def has_reject_report_permission(self, request, object_id=None):
        return self._awaits_decision(object_id)

    def has_resend_report_permission(self, request, object_id=None):
        return (
            self._run_field(object_id, "report_state") == AuditRun.ReportState.APPROVED
        )

    # Only offered once the send is provably stranded — showing it during a
    # healthy in-flight send would let an admin reset a run the worker is about
    # to mark sent, and the worker's transition would then fail.
    def has_reset_stranded_send_permission(self, request, object_id=None):
        run = AuditRun.objects.filter(pk=object_id).first()
        return run is not None and run.send_is_stranded

    def has_terminate_run_permission(self, request, object_id=None):
        return self._run_field(object_id, "status") == AuditRun.Status.RUNNING

    @action(
        description="Approve & send",
        url_path="approve-report",
        permissions=["approve_report"],
    )
    def approve_report(self, request, object_id):
        run = AuditRun.objects.get(pk=object_id)
        try:
            run.transition_report_to(AuditRun.ReportState.APPROVED)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            run.enqueue_send()
            self.message_user(request, f"Report for run #{run.pk} queued for sending.")
        return self._redirect_to_change(object_id)

    @action(
        description="Reject report",
        url_path="reject-report",
        permissions=["reject_report"],
    )
    def reject_report(self, request, object_id):
        run = AuditRun.objects.get(pk=object_id)
        try:
            run.transition_report_to(AuditRun.ReportState.REJECTED)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(
                request,
                f"Report for run #{run.pk} rejected; nothing sent. Sources are kept "
                f"so job #{run.job_id} can be re-run with another suite.",
                messages.WARNING,
            )
        return self._redirect_to_change(object_id)

    @action(
        description="Resend report",
        url_path="resend-report",
        permissions=["resend_report"],
    )
    def resend_report(self, request, object_id):
        run = AuditRun.objects.get(pk=object_id)
        if run.report_state != AuditRun.ReportState.APPROVED:
            self.message_user(
                request,
                f"Cannot resend: run #{run.pk} report is {run.report_state!r}, "
                "not 'approved'.",
                messages.ERROR,
            )
        else:
            run.enqueue_send()
            self.message_user(request, f"Report for run #{run.pk} re-queued.")
        return self._redirect_to_change(object_id)

    @action(
        description="Reset to approved",
        url_path="reset-send",
        permissions=["reset_stranded_send"],
    )
    def reset_stranded_send(self, request, object_id):
        """Unstick a report whose worker died mid-send (stuck `sending`)."""
        run = AuditRun.objects.get(pk=object_id)
        try:
            run.transition_report_to(AuditRun.ReportState.APPROVED)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(
                request,
                f"Report for run #{run.pk} reset to approved; use Resend to retry.",
                messages.WARNING,
            )
        return self._redirect_to_change(object_id)

    @action(
        description="Terminate execution",
        url_path="terminate",
        permissions=["terminate_run"],
    )
    def terminate_run(self, request, object_id):
        run = AuditRun.objects.get(pk=object_id)
        if run.status != AuditRun.Status.RUNNING:
            self.message_user(
                request,
                f"Cannot terminate: run #{run.pk} is {run.status}, not running.",
                messages.ERROR,
            )
        elif not run.celery_task_id:
            self.message_user(
                request,
                f"Cannot terminate: no task ID recorded for run #{run.pk}.",
                messages.ERROR,
            )
        else:
            current_app.control.revoke(run.celery_task_id, terminate=True)
            if run.terminate("Terminated by admin user"):
                self.message_user(request, f"Run #{run.pk} terminated.")
            else:
                run.refresh_from_db(fields=["status"])
                self.message_user(
                    request,
                    f"Run #{run.pk} finished as {run.status} before it could be "
                    "terminated; left unchanged.",
                    messages.WARNING,
                )
        return self._redirect_to_change(object_id)


@admin.register(AuditJob)
class AuditJobAdmin(ModelAdmin):
    list_display = [
        "id",
        "repo_full_name",
        "email",
        "state",
        "needs_attention",
        "keep_sources",
        "installation",
        "created_at",
    ]
    list_filter = ["state", "keep_sources"]
    search_fields = ["repo_full_name", "email"]
    readonly_fields = ["installation", "repo_full_name", "email", "created_at"]
    fieldsets = [
        (
            "Job",
            {
                "fields": [
                    "repo_full_name",
                    "email",
                    "state",
                    "keep_sources",
                    "created_at",
                ]
            },
        ),
        (
            "GitHub Installation",
            {"fields": ["installation"], "classes": ["collapse"]},
        ),
    ]
    ordering = ["-created_at"]
    actions = ["bulk_cleanup"]
    actions_detail = ["approve_job", "reject_job", "mark_failed", "cleanup_job"]

    def get_queryset(self, request):
        # Annotate once instead of one EXISTS query per row: needs_attention
        # otherwise runs obj.has_outstanding_runs per changelist row, and the
        # `or` only short-circuits for overdue jobs, so nearly every row pays it.
        return (
            super()
            .get_queryset(request)
            .annotate(
                _has_outstanding=Exists(
                    AuditRun.objects.filter(
                        AuditJob.outstanding_runs_q(), job=OuterRef("pk")
                    )
                )
            )
        )

    @admin.display(description="Needs attention", boolean=True)
    def needs_attention(self, obj):
        # CLONING past the task hard time limit — the clone worker likely died;
        # use "Mark failed". Or READY with no run left to act on: the job is
        # holding its clone until staff re-run it or delete sources, and until
        # then the submitter's funnel still counts it as active.
        #
        # Uses the get_queryset annotation when present (changelist); falls
        # back to the property for plain instances (change page, direct calls
        # in tests) where the annotation was never attached.
        has_outstanding = getattr(obj, "_has_outstanding", None)
        if has_outstanding is None:
            has_outstanding = obj.has_outstanding_runs
        return obj.is_overdue or (
            obj.state == AuditJob.State.READY and not has_outstanding
        )

    # Unfold renders a detail action only when its has_<name>_permission hook
    # passes. Admin access is already gated on is_staff, so these are not
    # authorization checks — they answer whether the transition is legal from
    # the object's current state, per VALID_TRANSITIONS.
    def _can_transition(self, object_id, target) -> bool:
        state = (
            AuditJob.objects.filter(pk=object_id)
            .values_list("state", flat=True)
            .first()
        )
        if state is None:
            return False
        return target in AuditJob.VALID_TRANSITIONS.get(state, [])

    def has_approve_job_permission(self, request, object_id=None):
        return self._can_transition(object_id, AuditJob.State.READY)

    def has_reject_job_permission(self, request, object_id=None):
        return self._can_transition(object_id, AuditJob.State.REJECTED)

    def has_mark_failed_permission(self, request, object_id=None):
        return self._can_transition(object_id, AuditJob.State.FAILED)

    def has_cleanup_job_permission(self, request, object_id=None):
        return self._can_transition(object_id, AuditJob.State.CLOSED)

    @action(description="Approve", url_path="approve", permissions=["approve_job"])
    def approve_job(self, request, object_id):
        job = AuditJob.objects.get(pk=object_id)
        try:
            job.approve()
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(request, f"Job #{job.pk} approved; run queued.")
        return self._redirect_to_change(request, object_id)

    @action(description="Reject", url_path="reject", permissions=["reject_job"])
    def reject_job(self, request, object_id):
        job = AuditJob.objects.get(pk=object_id)
        try:
            job.reject()
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(request, f"Job #{job.pk} rejected.", messages.WARNING)
        return self._redirect_to_change(request, object_id)

    @action(
        description="Mark failed", url_path="mark-failed", permissions=["mark_failed"]
    )
    def mark_failed(self, request, object_id):
        """Unstick a job whose clone worker died (stuck CLONING)."""
        job = AuditJob.objects.get(pk=object_id)
        try:
            job.transition_to(AuditJob.State.FAILED)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            job.delete_clone()
            self.message_user(
                request, f"Job #{job.pk} marked failed.", messages.WARNING
            )
        return self._redirect_to_change(request, object_id)

    @action(
        description="Delete sources", url_path="cleanup", permissions=["cleanup_job"]
    )
    def cleanup_job(self, request, object_id):
        job = AuditJob.objects.get(pk=object_id)

        # Revoke any running tasks first
        running_runs = job.runs.filter(status=AuditRun.Status.RUNNING)
        if running_runs.exists():
            for run in running_runs:
                if run.celery_task_id:
                    current_app.control.revoke(run.celery_task_id, terminate=True)
                    run.terminate("Job cleanup triggered while running")

            self.message_user(
                request,
                f"Terminated all running audits for job #{job.pk}.",
                messages.WARNING,
            )

        try:
            job.cleanup()
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(
                request, f"Sources deleted for job #{job.pk}.", messages.WARNING
            )
        return self._redirect_to_change(request, object_id)

    @admin.action(description="Delete sources for selected jobs")
    def bulk_cleanup(self, request, queryset):
        cleaned = 0
        skipped = 0
        for job in queryset:
            running_runs = job.runs.filter(status=AuditRun.Status.RUNNING)
            for run in running_runs:
                if run.celery_task_id:
                    current_app.control.revoke(run.celery_task_id, terminate=True)
                    run.terminate("Job cleanup triggered while running")
            try:
                job.cleanup()
            except ValueError:
                skipped += 1
            else:
                cleaned += 1
        self.message_user(
            request,
            f"Sources deleted for {cleaned} job(s); skipped {skipped} that "
            "cannot be cleaned up (not in a closeable state).",
        )

    def _redirect_to_change(self, request, object_id):
        return redirect(reverse("admin:audit_auditjob_change", args=[object_id]))


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass
