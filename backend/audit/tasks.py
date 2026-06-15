import git
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import Context, Template
from django.utils import timezone

from audit.ai.runner import run_pipeline
from audit.ai.suites import suite_to_agent_definitions
from audit.models import AgentRunOutput, AuditJob, AuditRun
from github_app.github import get_installation_token


@shared_task
def clone_repo(job_id: int) -> None:
    job = AuditJob.objects.select_related("installation").get(pk=job_id)
    job.transition_to(AuditJob.State.CLONING)

    try:
        token = get_installation_token(job.installation.installation_id)
        owner, _, repo = job.repo_full_name.partition("/")
        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        job.clone_path.mkdir(parents=True, exist_ok=True)
        git.Repo.clone_from(clone_url, job.clone_path)

        job.transition_to(AuditJob.State.AWAITING_APPROVAL)
    except Exception:
        job.transition_to(AuditJob.State.FAILED)
        raise


@shared_task
def cleanup_job_dir(job_id: int) -> None:
    try:
        job = AuditJob.objects.get(pk=job_id)
    except AuditJob.DoesNotExist:
        return
    job.delete_clone()


_PDF_ATTACHMENT_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB

_BUNDLED_EMAIL_TEMPLATE = (
    settings.BASE_DIR
    / "audit"
    / "templates"
    / "email"
    / "report_email.html"
)

_DEFAULT_SUBJECT = "VibeAudit Report — {{ repo_name }}"


def _render_email(run, pdf_attached: bool) -> tuple[str, str, str]:
    """Return (subject, plain_body, html_body) rendered for *run*.

    Resolution order for html_body:
      1. Non-blank email_html_body on the run's suite (admin-editable)
      2. Bundled compiled MJML template on disk
    The plain_body is always built directly from context variables.
    """
    repo_name = run.job.repo_full_name
    ctx = Context(
        {
            "repo_name": repo_name,
            "summary": run.summary,
            "run_status": run.status,
            "suite_name": run.suite.name,
            "pdf_attached": pdf_attached,
            "site_url": settings.SITE_URL,
        },
        autoescape=False,
    )

    subject_tpl = run.suite.email_subject or _DEFAULT_SUBJECT
    subject = Template(subject_tpl).render(ctx)

    if run.suite.email_html_body:
        html_body = Template(run.suite.email_html_body).render(ctx)
    else:
        raw = _BUNDLED_EMAIL_TEMPLATE.read_text(encoding="utf-8")
        html_body = Template(raw).render(ctx)

    plain_body = f"Your audit report for {repo_name} is ready."

    return subject, plain_body, html_body


def _send_report_email(run) -> None:
    """Send the audit report email to job.email.  No-op if email is blank.

    If the PDF exceeds _PDF_ATTACHMENT_SIZE_LIMIT the attachment is omitted and
    the email body reflects that.
    """
    if not run.job.email:
        return

    from audit.pdf import render_pdf  # lazy import — requires system pango/gobject libs

    pdf_bytes = render_pdf(run)
    pdf_attached = len(pdf_bytes) <= _PDF_ATTACHMENT_SIZE_LIMIT

    subject, plain_body, html_body = _render_email(run, pdf_attached=pdf_attached)

    msg = EmailMultiAlternatives(subject=subject, body=plain_body, to=[run.job.email])
    if settings.EMAIL_BACKEND != "django.core.mail.backends.console.EmailBackend":
        msg.attach_alternative(html_body, "text/html")
    if pdf_attached:
        msg.attach("report.pdf", pdf_bytes, "application/pdf")
    msg.send()


@shared_task(bind=True)
def execute_audit_run(self, run_id: int) -> None:
    run = AuditRun.objects.select_related("job", "suite").get(pk=run_id)
    job = run.job
    suite = run.suite

    # Pre-flight: reject unknown models before touching the pipeline
    available = settings.AVAILABLE_AI_MODELS
    if available and suite.model not in available:
        run.status = AuditRun.Status.FAILED
        run.error = (
            f"Model '{suite.model}' is not in AVAILABLE_AI_MODELS. "
            "Update the suite or add the model to settings."
        )
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
        return

    # Store the Celery task ID for tracking/revocation (None if called directly in tests)
    run.status = AuditRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    try:
        agents = suite_to_agent_definitions(suite)
        result = run_pipeline(
            job, agents, suite.model, suite.orchestrator_prompt or None
        )
        report = result.report

        run.summary = report.summary
        run.markdown = report.markdown
        run.status = AuditRun.Status.COMPLETED
        run.finished_at = timezone.now()
        run.save(update_fields=["summary", "markdown", "status", "finished_at"])

        AgentRunOutput.objects.bulk_create(
            [
                AgentRunOutput(
                    run=run, agent_id=cap.agent_id, output=cap.output, position=i
                )
                for i, cap in enumerate(result.agent_outputs)
            ]
        )

        _send_report_email(run)
    except Exception as exc:  # noqa: BLE001
        run.status = AuditRun.Status.FAILED
        run.error = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
    finally:
        if not job.keep_sources:
            job.cleanup()
