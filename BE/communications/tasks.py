from celery import shared_task
from django.conf import settings

from .whatsapp import deliver_one
from .whatsapp_provider import CloudAPI


@shared_task
def deliver_whatsapp():
    if settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
        provider = CloudAPI()
        for _ in range(50):
            if not deliver_one(provider):
                break
