from celery import shared_task

from .whatsapp import deliver_one
from .whatsapp_provider import CloudAPI


@shared_task
def deliver_whatsapp():
    for _ in range(50):
        provider = CloudAPI()
        if not provider.config['enabled'] or not deliver_one(provider):
            break
