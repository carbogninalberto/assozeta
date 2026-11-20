import json
import logging
import re
from datetime import timedelta
from uuid import UUID

from instance.integration_configuration import effective_integration
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .message_templates import international_phone
from .models import WhatsAppDelivery, WhatsAppReceipt
from .whatsapp_provider import ProviderError

logger = logging.getLogger(__name__)
STATUS_ORDER = ('queued', 'sent', 'failed', 'delivered', 'read')
TEMPLATE_ROUTE = re.compile(r'([a-z0-9_]+)/([A-Za-z0-9_]+)(/message)?')


def queue_notifications(user, messages):
    try:
        # A savepoint isolates settings and outbox failures from the triggering transaction.
        with transaction.atomic():
            config = effective_integration('whatsapp')
            if not config['enabled'] and config['source'] == 'instance':
                return
            routes = json.loads(config['notification_routes'])
            if not routes:
                return
            for message in messages:
                route = routes.get(message.get('type'))
                if route is None:
                    continue
                recipient = international_phone(user.phone)
                row = WhatsAppDelivery(event_type=message['type'], recipient=recipient or (user.phone or ''),
                                       body=message['msg'])
                if route != 'text':
                    match = TEMPLATE_ROUTE.fullmatch(route) if isinstance(route, str) else None
                    if match:
                        row.template_name, row.template_language, parameter = match.groups()
                        if not parameter:
                            row.body = ''
                    else:
                        row.error_code, row.error_message = 'configuration', 'Invalid WhatsApp notification route'
                if not config['phone_number_id'] or not config['access_token']:
                    row.error_code, row.error_message = 'configuration', 'Missing WhatsApp resource ID or access token'
                if not recipient:
                    row.error_code, row.error_message = 'invalid_recipient', 'International phone number required'
                if row.error_code:
                    row.status = 'failed'
                    logger.warning('WhatsApp enqueue failed id=%s code=%s', row.id, row.error_code)
                row.save()
    except Exception:
        logger.error('Could not enqueue WhatsApp notification')


@transaction.atomic
def deliver_one(provider):
    row = (WhatsAppDelivery.objects.select_for_update(skip_locked=True)
           .filter(status='queued', next_attempt_at__lte=timezone.now()).order_by('next_attempt_at', 'id').first())
    if row is None:
        return False
    row.attempts += 1
    try:
        if row.template_name:
            row.provider_message_id = provider.send_template(row.recipient, row.template_name, row.template_language,
                                                             row.body, row.id)
        else:
            row.provider_message_id = provider.send_text(row.recipient, row.body, row.id)
        row.status, row.error_code, row.error_message = 'sent', '', ''
    except ProviderError as error:
        row.error_code, row.error_message = error.code, str(error)
        if error.transient and row.attempts < 3:
            row.next_attempt_at = timezone.now() + timedelta(seconds=30 * 2 ** (row.attempts - 1))
        else:
            row.status = 'failed'
        logger.warning('WhatsApp send failed id=%s attempt=%s code=%s', row.id, row.attempts, row.error_code)
    row.updated_at = timezone.now()
    row.save()
    return True


@transaction.atomic
def record_status(event):
    message_id = event['provider_message_id']
    query = Q(provider_message_id=message_id)
    try:
        reference = UUID(str(event['reference']))
    except ValueError:
        pass
    else:
        query = Q(pk=reference)
    row = WhatsAppDelivery.objects.select_for_update().filter(query).first()
    if row is None:
        return
    _, created = WhatsAppReceipt.objects.get_or_create(
        delivery=row, provider_message_id=message_id, status=event['status'],
        defaults={key: event[key] for key in ('occurred_at', 'error_code', 'error_message')},
    )
    if created and (row.provider_message_id is None or STATUS_ORDER.index(event['status']) > STATUS_ORDER.index(row.status)):
        row.provider_message_id = row.provider_message_id or message_id
        row.status, row.error_code, row.error_message = event['status'], event['error_code'], event['error_message']
        row.updated_at = timezone.now()
        row.save()
        if row.status == 'failed':
            logger.warning('WhatsApp delivery failed id=%s code=%s', row.id, row.error_code)
