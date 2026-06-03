import git
from celery import shared_task

from audit.ai.pipeline import run_pipeline
from audit.models import AuditJob
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


@shared_task(bind=True)
def execute_audit_run(self, run_id: int) -> None:
    from django.utils import timezone

    from audit.ai.suites import suite_to_agent_definitions
    from audit.models import AgentRunOutput, AuditRun

    run = AuditRun.objects.select_related("job", "suite").get(pk=run_id)
    job = run.job

    # Store the Celery task ID for tracking/revocation (None if called directly in tests)
    celery_task_id = getattr(self.request, 'id', None) if hasattr(self, 'request') else None
    run.celery_task_id = celery_task_id or ''
    run.status = AuditRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["celery_task_id", "status", "started_at"])

    try:
        agents = suite_to_agent_definitions(run.suite)
        result = run_pipeline(job, agents, run.suite.orchestrator_prompt or None)
        report = result.report

        # Build the PDF before marking COMPLETED so a PDF failure marks the run FAILED.
        if run.generate_pdf:
            from audit.ai.report.typst_builder import build_pdf

            build_pdf(report, job.job_dir / f"run_{run.pk}.pdf")

        run.summary = report.summary
        run.markdown = report.markdown
        run.status = AuditRun.Status.COMPLETED
        run.finished_at = timezone.now()
        run.save(
            update_fields=["summary", "markdown", "status", "finished_at"]
        )

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
    finally:
        if not job.keep_sources:
            job.cleanup()
