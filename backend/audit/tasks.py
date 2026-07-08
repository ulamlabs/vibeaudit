import logging
import shutil
from decimal import Decimal

import git
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from langgraph.errors import GraphRecursionError

from audit.ai.budget import CostBudgetCallback, CostBudgetExceeded
from audit.ai.runner import run_pipeline
from audit.ai.suites import suite_to_agent_definitions
from audit.email import (
    send_clone_failure_notification,
    send_failure_email,
    send_new_submission_notification,
    send_report_email,
)
from audit.models import AgentRunOutput, AuditJob, AuditRun
from github_app.github import get_installation_token

logger = logging.getLogger(__name__)


def _measured_cost(cost_callback: CostBudgetCallback) -> Decimal | None:
    if not cost_callback.tracked:
        return None
    return Decimal(str(round(cost_callback.total, 4)))


@shared_task
def clone_repo(job_id: int) -> None:
    job = AuditJob.objects.select_related("installation").get(pk=job_id)
    job.transition_to(AuditJob.State.CLONING)

    try:
        token = get_installation_token(job.installation.installation_id)
        owner, _, repo = job.repo_full_name.partition("/")
        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        job.clone_path.mkdir(parents=True, exist_ok=True)
        git.Repo.clone_from(clone_url, job.clone_path, depth=1)
        try:
            shutil.rmtree(job.clone_path / ".git")
        except Exception:
            logger.warning(
                "Failed to remove .git dir for job %s", job_id, exc_info=True
            )

        job.transition_to(AuditJob.State.AWAITING_APPROVAL)
    except Exception as exc:
        # Keep the installation live on failure so the visitor can retry (and an
        # admin could re-clone later); only a successful clone uninstalls.
        # The submitter is not notified (anonymous funnel) — staff are, so they
        # can react.
        job.transition_to(AuditJob.State.FAILED)
        send_clone_failure_notification(job, reason=str(exc))
        raise

    # Clone is on disk — drop read-only GitHub access. Even with keep_sources,
    # re-runs reuse the local clone, so we never need GitHub again. Skip while
    # another job on this installation still needs GitHub (hasn't finished its
    # own clone); the last clone to finish uninstalls. Best-effort so a failed
    # uninstall never fails the audit; logger.exception so Sentry reports it.
    other_jobs_need_github = (
        job.installation.audit_jobs.exclude(pk=job.pk)
        .filter(state__in=[AuditJob.State.PENDING, AuditJob.State.CLONING])
        .exists()
    )
    if not other_jobs_need_github:
        try:
            job.installation.uninstall()
        except Exception:
            logger.exception("Failed to uninstall installation for job %s", job_id)

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
    # Atomically claim the run (PENDING → RUNNING). A redelivered message or a
    # duplicate enqueue finds it already claimed/finished and skips, so a
    # completed run is never re-executed (and never re-emailed).
    claimed = AuditRun.objects.filter(
        pk=run_id, status=AuditRun.Status.PENDING
    ).update(status=AuditRun.Status.RUNNING, started_at=timezone.now())
    if not claimed:
        logger.warning(
            "Run %s is not PENDING (duplicate delivery or already terminated); "
            "skipping execution.",
            run_id,
        )
        return

    run = AuditRun.objects.select_related("job", "suite").get(pk=run_id)
    job = run.job
    suite = run.suite

    recursion_limit, max_run_cost = suite.resolve_ai_run_limits()
    cost_callback = CostBudgetCallback(max_run_cost, suite.model)

    try:
        available = settings.AVAILABLE_AI_MODELS
        if available and suite.model not in available:
            raise ValueError(
                f"Model '{suite.model}' is not in AVAILABLE_AI_MODELS. "
                "Update the suite or add the model to settings."
            )

        agents = suite_to_agent_definitions(suite)
        result = run_pipeline(
            job,
            agents,
            suite.model,
            suite.orchestrator_prompt or None,
            run_id=run_id,
            recursion_limit=recursion_limit,
            cost_callback=cost_callback,
        )
        report = result.report

        # Guarded like terminate(): if an admin terminated the run mid-flight,
        # don't overwrite FAILED with COMPLETED (the status check below then
        # also skips the report email).
        AuditRun.objects.filter(pk=run.pk, status=AuditRun.Status.RUNNING).update(
            summary=report.summary,
            markdown=report.markdown,
            status=AuditRun.Status.COMPLETED,
            cost_usd=_measured_cost(cost_callback),
            finished_at=timezone.now(),
        )
        run.refresh_from_db(
            fields=["summary", "markdown", "status", "cost_usd", "finished_at"]
        )

        AgentRunOutput.objects.bulk_create(
            [
                AgentRunOutput(
                    run=run, agent_id=cap.agent_id, output=cap.output, position=i
                )
                for i, cap in enumerate(result.agent_outputs)
            ]
        )
    except (CostBudgetExceeded, GraphRecursionError) as exc:
        if isinstance(exc, CostBudgetExceeded):
            reason = (
                f"Run stopped by guard: estimated cost ${exc.used:.2f} would exceed "
                f"the budget of ${exc.budget:.2f}."
            )
        else:
            # Append the exception text rather than the local recursion_limit: when
            # it is 0 the config omits the key and LangGraph enforces its own default,
            # so the local var may not match the limit that actually fired.
            reason = (
                f"Run stopped by guard: orchestrator recursion limit reached. {exc}"
            )
        run.terminate(reason, cost_usd=_measured_cost(cost_callback))
        send_failure_email(run)
    except Exception as exc:  # noqa: BLE001
        run.terminate(str(exc), cost_usd=_measured_cost(cost_callback))
        send_failure_email(run)
    finally:
        if not job.keep_sources:
            job.cleanup()

    if run.status == AuditRun.Status.COMPLETED:
        send_report_email(run)
