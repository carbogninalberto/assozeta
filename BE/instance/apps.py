from django.apps import AppConfig
from django.db.models.signals import post_migrate


class InstanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'instance'
    verbose_name = 'Instance Configuration'

    def ready(self):
        from .operational_schema import apply_operational_schema
        from .sso.logging import install

        post_migrate.connect(apply_operational_schema, sender=self, dispatch_uid='assozeta_operational_schema')
        install()
