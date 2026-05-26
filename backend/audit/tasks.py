from pathlib import Path

import git
from celery import shared_task
from django.conf import settings

from audit.models import AuditJob
from github_app.github import get_installation_token


@shared_task(bind=True)
def clone_repo(self, job_id: int) -> None:
    job = AuditJob.objects.select_related("installation").get(pk=job_id)
    job.state = AuditJob.State.CLONING
    job.save(update_fields=["state"])

    try:
        token = get_installation_token(job.installation.installation_id)
        owner, _, repo = job.repo_full_name.partition("/")
        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        clone_path = Path(settings.REPOS_DIR) / str(job.pk) / job.repo_full_name
        clone_path.mkdir(parents=True, exist_ok=True)

        git.Repo.clone_from(clone_url, clone_path)

        job.state = AuditJob.State.AWAITING_APPROVAL
        job.save(update_fields=["state"])
    except Exception:
        job.state = AuditJob.State.FAILED
        job.save(update_fields=["state"])
        raise
