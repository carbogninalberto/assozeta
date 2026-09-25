from celery import shared_task

from django.db import transaction
from .models import BakneyPairing
from .protocol import SSOError
from .service import deliver_revocations, locked_pairing, synchronize


@shared_task(ignore_result=True)
def maintain_bakney_pairings():
    deliver_revocations()
    if BakneyPairing.objects.filter(state__in=('pending', 'paired'), remote_pairing_id__isnull=False).exists():
        with transaction.atomic():
            try:
                pairing = locked_pairing()
                if pairing.state in ('pending', 'paired') and pairing.remote_pairing_id:
                    synchronize(pairing)
            except SSOError:
                pass  # Retain last checked_at; login always requires a live check.
