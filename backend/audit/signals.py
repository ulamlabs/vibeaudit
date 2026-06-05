from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import AuditJob
from .tasks import cleanup_job_dir


@receiver(post_delete, sender=AuditJob)
def cleanup_on_delete(sender, instance, **kwargs):
    cleanup_job_dir.delay(instance.pk)
