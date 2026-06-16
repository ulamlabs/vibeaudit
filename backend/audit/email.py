"""Email rendering and sending for audit events."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.template import Context, Template

from audit.pdf import render_pdf

logger = logging.getLogger(__name__)

_IS_CONSOLE_EMAIL = (
    settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"
)

_BUNDLED_EMAIL_DIR = settings.BASE_DIR / "audit" / "templates" / "email"

_DEFAULT_REPORT_SUBJECT = settings.REPORT_EMAIL_SUBJECT
_DEFAULT_FAILURE_SUBJECT = "VibeAudit Audit Failed"
_DEFAULT_SUBMISSION_SUBJECT = "New VibeAudit Submission"


def _load_email_template(filename: str) -> str:
    extra_dir = settings.EXTRA_EMAIL_TEMPLATES_DIR
    if extra_dir:
        candidate = extra_dir / filename
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return (_BUNDLED_EMAIL_DIR / filename).read_text(encoding="utf-8")


def _send(msg: EmailMultiAlternatives, html_body: str) -> None:
    if not _IS_CONSOLE_EMAIL:
        msg.attach_alternative(html_body, "text/html")
    msg.send()


def render_report_email(run, pdf_attached: bool) -> tuple[str, str, str]:
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

    subject = _DEFAULT_REPORT_SUBJECT

    if run.suite.email_html_body:
        html_body = Template(run.suite.email_html_body).render(ctx)
    else:
        html_body = Template(_load_email_template("report_email.html")).render(ctx)

    plain_body = f"Your audit report for {repo_name} is ready."
    return subject, plain_body, html_body


def send_report_email(run) -> None:
    """Send the audit report to job.email with PDF attachment. No-op if email is blank."""
    if not run.job.email:
        return

    _PDF_LIMIT = 10 * 1024 * 1024
    pdf_bytes = render_pdf(run)
    pdf_attached = len(pdf_bytes) <= _PDF_LIMIT

    subject, plain_body, html_body = render_report_email(run, pdf_attached=pdf_attached)

    msg = EmailMultiAlternatives(subject=subject, body=plain_body, to=[run.job.email])
    if pdf_attached:
        msg.attach("report.pdf", pdf_bytes, "application/pdf")
    _send(msg, html_body)


def send_failure_email(run) -> None:
    """Notify the submitter that their audit run failed. No-op if email is blank."""
    if not run.job.email:
        return

    repo_name = run.job.repo_full_name
    ctx = Context(
        {"repo_name": repo_name, "run_id": run.pk, "site_url": settings.SITE_URL},
        autoescape=False,
    )
    html_body = Template(_load_email_template("failure_email.html")).render(ctx)
    plain_body = (
        f"Your audit for {repo_name} failed (run ID: {run.pk}). "
        "Please contact the administrator for assistance."
    )

    msg = EmailMultiAlternatives(
        subject=Template(_DEFAULT_FAILURE_SUBJECT).render(ctx),
        body=plain_body,
        to=[run.job.email],
    )
    try:
        _send(msg, html_body)
    except Exception:
        logger.exception("Failed to send failure email for run %s", run.pk)


def send_new_submission_notification(job) -> None:
    """Notify is_staff + audit_notifications group members about a new job awaiting approval."""
    User = get_user_model()
    recipients = [
        email
        for email in User.objects.filter(
            is_staff=True, groups__name="audit_notifications"
        )
        .values_list("email", flat=True)
        .distinct()
        if email
    ]
    if not recipients:
        return

    ctx = Context(
        {
            "repo_name": job.repo_full_name,
            "submitter_email": job.email,
            "job_id": job.pk,
            "site_url": settings.SITE_URL,
        },
        autoescape=False,
    )
    html_body = Template(_load_email_template("new_submission_email.html")).render(ctx)
    plain_body = (
        f"New audit submission for {job.repo_full_name} "
        f"by {job.email} (job ID: {job.pk})."
    )

    msg = EmailMultiAlternatives(
        subject=Template(_DEFAULT_SUBMISSION_SUBJECT).render(ctx),
        body=plain_body,
        to=recipients,
    )
    try:
        _send(msg, html_body)
    except Exception:
        logger.exception("Failed to send staff notification for job %s", job.pk)
