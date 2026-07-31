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
_DEFAULT_SUBMISSION_SUBJECT = "New VibeAudit Submission"
_DEFAULT_CLONE_FAILURE_SUBJECT = "VibeAudit Clone Failed"
_DEFAULT_REPORT_APPROVAL_SUBJECT = "VibeAudit Report Awaiting Approval"
_DEFAULT_RUN_FAILURE_SUBJECT = "VibeAudit Audit Run Failed"


def _staff_notification_recipients() -> list[str]:
    """Emails of is_staff members of the audit_notifications group."""
    User = get_user_model()
    return [
        email
        for email in User.objects.filter(
            is_staff=True, groups__name="audit_notifications"
        )
        .values_list("email", flat=True)
        .distinct()
        if email
    ]


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
        }
    )

    subject = _DEFAULT_REPORT_SUBJECT

    if run.suite.email_html_body:
        html_body = Template(run.suite.email_html_body).render(ctx)
    else:
        html_body = Template(_load_email_template("report_email.html")).render(ctx)

    plain_body = f"Your audit report for {repo_name} is ready."
    return subject, plain_body, html_body


def send_report_email(run) -> None:
    """
    Send the audit report to job.email with PDF attachment. No-op if email is
    blank. Errors propagate: the caller leaves report_state at 'approved' so the
    admin's Resend action can retry.
    """
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


def send_report_approval_notification(run) -> None:
    """Tell staff a completed report is held pending a human decision."""
    try:
        recipients = _staff_notification_recipients()
        if not recipients:
            return

        ctx = Context(
            {
                "repo_name": run.job.repo_full_name,
                "suite_name": run.suite.name,
                "summary": run.summary,
                "cost_usd": run.cost_usd,
                "run_id": run.pk,
                "site_url": settings.SITE_URL,
            }
        )
        html_body = Template(
            _load_email_template("report_approval_email.html")
        ).render(ctx)
        plain_body = (
            f"The audit report for {run.job.repo_full_name} (run #{run.pk}) is "
            "complete and awaiting approval before it is sent to the submitter."
        )
        msg = EmailMultiAlternatives(
            subject=_DEFAULT_REPORT_APPROVAL_SUBJECT,
            body=plain_body,
            to=recipients,
        )
        _send(msg, html_body)
    except Exception:
        logger.exception(
            "Failed to send report-approval notification for run %s", run.pk
        )


def send_run_failure_notification(run) -> None:
    """
    Tell staff a run failed. The submitter is an anonymous funnel visitor who
    cannot act on it, so only staff are notified and they decide out of band
    what, if anything, to tell the submitter.
    """
    try:
        recipients = _staff_notification_recipients()
        if not recipients:
            return

        ctx = Context(
            {
                "repo_name": run.job.repo_full_name,
                "suite_name": run.suite.name,
                "submitter_email": run.job.email,
                "reason": run.error,
                "run_id": run.pk,
                "site_url": settings.SITE_URL,
            }
        )
        html_body = Template(_load_email_template("run_failure_email.html")).render(ctx)
        plain_body = (
            f"Audit run #{run.pk} for {run.job.repo_full_name} failed: "
            f"{run.error}. The submitter has NOT been notified."
        )
        msg = EmailMultiAlternatives(
            subject=_DEFAULT_RUN_FAILURE_SUBJECT,
            body=plain_body,
            to=recipients,
        )
        _send(msg, html_body)
    except Exception:
        logger.exception("Failed to send run-failure notification for run %s", run.pk)


def send_new_submission_notification(job) -> None:
    """Notify is_staff + audit_notifications group members about a new job awaiting approval."""
    recipients = _staff_notification_recipients()
    if not recipients:
        return

    ctx = Context(
        {
            "repo_name": job.repo_full_name,
            "submitter_email": job.email,
            "job_id": job.pk,
            "site_url": settings.SITE_URL,
        }
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


def send_clone_failure_notification(job, reason: str) -> None:
    """
    Notify staff that a job died at the clone stage, reusing the failure email
    template with the reason filled in (the customer-facing variant omits it).
    The submitter is an anonymous funnel visitor who can't act on
    infrastructure failures, so only staff are told and can react.
    """
    # Best-effort throughout: this runs in clone_repo's failure path, so any
    # error here (recipient lookup, template load/render, or send) must not mask
    # the original clone exception or block its re-raise.
    try:
        recipients = _staff_notification_recipients()
        if not recipients:
            return

        ctx = Context(
            {
                "repo_name": job.repo_full_name,
                "run_id": f"job-{job.pk}",
                "reason": reason,
                "site_url": settings.SITE_URL,
            }
        )
        html_body = Template(_load_email_template("failure_email.html")).render(ctx)
        plain_body = (
            f"Cloning failed for audit job #{job.pk} ({job.repo_full_name}), "
            f"submitted by {job.email}: {reason}. The job is marked failed and "
            "the submitter has NOT been notified."
        )
        msg = EmailMultiAlternatives(
            subject=_DEFAULT_CLONE_FAILURE_SUBJECT,
            body=plain_body,
            to=recipients,
        )
        _send(msg, html_body)
    except Exception:
        logger.exception("Failed to send clone-failure notification for job %s", job.pk)
