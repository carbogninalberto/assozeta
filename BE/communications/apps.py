from django.apps import AppConfig


class CommunicationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'communications'

    def ready(self):
        from django.conf import settings
        from core.celery import app

        if settings.WHATSAPP_PHONE_NUMBER_ID and settings.WHATSAPP_ACCESS_TOKEN:
            app.add_periodic_task(10.0, app.signature('communications.tasks.deliver_whatsapp'),
                                  name='deliver-whatsapp', expires=10)
