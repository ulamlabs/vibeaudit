from .models import AuditJob
from .tasks import cleanup_job_dir, run_audit


def approve_job(job: AuditJob) -> None:
    job.approve()
    run_audit.delay(job.pk)


def reject_job(job: AuditJob) -> None:
    job.reject()
    cleanup_job_dir.delay(job.pk)
