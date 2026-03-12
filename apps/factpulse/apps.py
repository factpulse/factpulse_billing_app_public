from django.apps import AppConfig


class FactpulseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.factpulse"
    verbose_name = "FactPulse Integration"

    def ready(self):
        import apps.factpulse.signals  # noqa: F401
