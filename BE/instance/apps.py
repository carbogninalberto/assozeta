from django.apps import AppConfig


class InstanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'instance'
    verbose_name = 'Instance Configuration'

    def ready(self):
        from .sso.logging import install
        install()
