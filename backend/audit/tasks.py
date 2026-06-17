import git
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from audit.ai.runner import run_pipeline
from audit.ai.suites import suite_to_agent_definitions
from audit.email import (
    send_failure_email,
    send_new_submission_notification,
    send_report_email,
)
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

    send_new_submission_notification(job)


@shared_task
def cleanup_job_dir(job_id: int) -> None:
    try:
        job = AuditJob.objects.get(pk=job_id)
    except AuditJob.DoesNotExist:
        return
    job.delete_clone()


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

    run.status = AuditRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    try:
        agents = suite_to_agent_definitions(suite)
        result = run_pipeline(
            job, agents, suite.model, suite.orchestrator_prompt or None, run_id=run_id
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
    except Exception as exc:  # noqa: BLE001
        run.status = AuditRun.Status.FAILED
        run.error = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
        send_failure_email(run)
    finally:
        if not job.keep_sources:
            job.cleanup()

    if run.status == AuditRun.Status.COMPLETED:
        send_report_email(run)
