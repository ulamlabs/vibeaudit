from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import AuditJob
from .tasks import delete_job_dir


@receiver(post_delete, sender=AuditJob)
def cleanup_on_delete(sender, instance, **kwargs):
    job_id = instance.pk
    transaction.on_commit(lambda: delete_job_dir.delay(job_id))
