from django.db.models.signals import pre_delete
from django.dispatch import receiver

from github_app.github import InstallationNotFoundError, delete_installation
from github_app.models import Installation


@receiver(pre_delete, sender=Installation)
def remote_delete_on_installation_delete(sender, instance, **kwargs):
    try:
        delete_installation(instance.installation_id)
    except InstallationNotFoundError:
        pass
