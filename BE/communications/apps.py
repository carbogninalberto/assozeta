from django.apps import AppConfig


class CommunicationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'communications'

    def ready(self):
        from core.celery import app

        app.add_periodic_task(10.0, app.signature('communications.tasks.deliver_whatsapp'),
                              name='deliver-whatsapp', expires=10)
