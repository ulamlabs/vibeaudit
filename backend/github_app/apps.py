from django.apps import AppConfig


class GithubAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "github_app"

    def ready(self):
        import github_app.signals  # noqa: F401
