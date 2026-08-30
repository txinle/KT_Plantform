from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api"

    def ready(self):
        from . import schema  # noqa: F401
        from . import rollout  # noqa: F401
