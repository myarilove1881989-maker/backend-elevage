from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Avoid database queries at app import time.
        # Superuser bootstrap is handled by a dedicated management command.
        pass