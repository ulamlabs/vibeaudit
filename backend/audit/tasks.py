import time

import git
from celery import shared_task

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
def run_audit(job_id: int) -> None:
    job = AuditJob.objects.get(pk=job_id)
    job.transition_to(AuditJob.State.RUNNING)

    try:
        # TODO: replace with real audit logic
        time.sleep(5)
        job.report = "Yey!"
        job.save(update_fields=["report"])
        job.transition_to(AuditJob.State.COMPLETED)
    finally:
        # Always clean up the clone regardless of outcome — report is persisted to DB above.
        cleanup_job_dir.delay(job_id)


@shared_task
def cleanup_job_dir(job_id: int) -> None:
    try:
        job = AuditJob.objects.get(pk=job_id)
    except AuditJob.DoesNotExist:
        return
    job.delete_clone()
