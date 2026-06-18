from django import forms
from celery import current_app
from django.contrib import admin, messages
from django.conf import settings as django_settings
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from audit.ai.prompts import ORCHESTRATOR_SYSTEM_PROMPT
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
    readonly_fields = ["effective_orchestrator_prompt"]

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

    @admin.display(description="Effective Orchestrator Prompt")
    def effective_orchestrator_prompt(self, obj):
        # Show the actual prompt that will be used (override or default).
        prompt = obj.orchestrator_prompt or ORCHESTRATOR_SYSTEM_PROMPT
        return format_html(
            '<pre style="white-space: pre-wrap; word-break: break-word; '
            'font-size: 0.85em; max-height: 400px; overflow-y: auto">{}</pre>',
            prompt,
        )


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
    list_display = ["id", "job", "suite", "status", "created_at"]
    list_filter = ["status", "suite", "job"]
    search_fields = ["job__repo_full_name"]
    inlines = [AgentRunOutputInline]
    readonly_fields = [
        "status",
        "summary",
        "report_html",
        "report_raw",
        "error",
        "created_at",
        "started_at",
        "finished_at",
        "celery_task_id",
    ]
    actions_detail = ["terminate_run", "download_pdf"]

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
        return [
            "job",
            "suite",
            "status",
            "summary",
            "report_html",
            "report_raw",
            "error",
            "celery_task_id",
            "created_at",
            "started_at",
            "finished_at",
        ]

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

    @action(description="Terminate execution", url_path="terminate")
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
            run.terminate("Terminated by admin user")
            self.message_user(request, f"Run #{run.pk} terminated.")
        return redirect(reverse("admin:audit_auditrun_change", args=[object_id]))


@admin.register(AuditJob)
class AuditJobAdmin(ModelAdmin):
    list_display = [
        "id",
        "repo_full_name",
        "email",
        "state",
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
    actions_detail = ["approve_job", "reject_job", "cleanup_job"]

    @action(description="Approve", url_path="approve")
    def approve_job(self, request, object_id):
        job = AuditJob.objects.get(pk=object_id)
        try:
            job.approve()
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(request, f"Job #{job.pk} approved; run queued.")
        return self._redirect_to_change(request, object_id)

    @action(description="Reject", url_path="reject")
    def reject_job(self, request, object_id):
        job = AuditJob.objects.get(pk=object_id)
        try:
            job.reject()
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(request, f"Job #{job.pk} rejected.", messages.WARNING)
        return self._redirect_to_change(request, object_id)

    @action(description="Delete sources", url_path="cleanup")
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

        job.cleanup()
        self.message_user(
            request, f"Sources deleted for job #{job.pk}.", messages.WARNING
        )
        return self._redirect_to_change(request, object_id)

    @admin.action(description="Delete sources for selected jobs")
    def bulk_cleanup(self, request, queryset):
        count = 0
        for job in queryset:
            running_runs = job.runs.filter(status=AuditRun.Status.RUNNING)
            for run in running_runs:
                if run.celery_task_id:
                    current_app.control.revoke(run.celery_task_id, terminate=True)
                    run.terminate("Job cleanup triggered while running")
            job.cleanup()
            count += 1
        self.message_user(request, f"Sources deleted for {count} job(s).")

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
