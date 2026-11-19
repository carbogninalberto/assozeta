import logging
import re
from urllib.parse import quote
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from application.models import Associate, SportAssociation, User
from application.permissions import IsAssociationUser
from core.middleware import IsAuthenticated

from .message_templates import (
    SAMPLE, VARIABLES, association_templates, channel_code, choose_template, invalid, locale_code,
    message_context, message_text, rendered_message, save_template, template_data, validate_template,
)
from .models import CommunicationConfiguration, OutgoingMessage

logger = logging.getLogger(__name__)

WARNINGS = {
    'discarded': 'Anteprima scartata. L’invio automatico è stato annullato.',
    'manual': 'Invio automatico saltato. Completa l’invio in WhatsApp.',
    'skipped': 'Invio automatico saltato. Nessuna integrazione è disponibile per questo canale.',
    'failed': 'Il servizio di invio non ha confermato la richiesta.',
}


def association_for(request):
    scope = request.user
    association = SportAssociation.objects.filter(user=scope, deleted=False).first()
    scoped = getattr(request, 'impersonation_association', None)
    if scope.role != User.ASSOCIATION or not scope.is_active or scope.deleted or not association or (
        scoped and scoped.pk != association.pk
    ):
        raise PermissionDenied('Accesso negato.')
    return association


def outgoing_association(request):
    association = association_for(request)
    if getattr(request, 'collaborator', False):
        collaborator = request.effective_user
        permission = 'association.personas.read'
        if collaborator.collaborator_role != User.FULL and permission not in (collaborator.collaborator_permissions or []):
            raise PermissionDenied(f'Missing permission: {permission}')
    return association


def identifier(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        result = UUID(value)
        if str(result) != value.lower():
            raise ValueError
        return result
    except ValueError:
        raise NotFound() from None


def request_data(request):
    if not isinstance(request.data, dict):
        raise ValidationError({'detail': 'Invia un oggetto JSON.'})
    return request.data


@extend_schema(methods=['GET'], responses=OpenApiTypes.OBJECT)
@extend_schema(methods=['POST'], request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT})
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsAssociationUser])
@parser_classes([JSONParser])
def message_template_list(request):
    association = association_for(request)
    if request.method == 'GET':
        templates = association_templates(association).order_by('channel', 'locale', 'id')
        return Response({'data': [template_data(row) for row in templates], 'variables': VARIABLES})
    template = save_template(association, request.original_user.pk, request_data(request))
    return Response({'data': template_data(template)}, status=201)


@extend_schema(methods=['PATCH'], request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsAssociationUser])
@parser_classes([JSONParser])
def message_template_detail(request, template_id):
    association = association_for(request)
    template_id = identifier(template_id)
    if request.method == 'PATCH':
        template = save_template(association, request.original_user.pk, request_data(request), template_id)
        if not template:
            raise NotFound()
        return Response({'data': template_data(template)})

    with transaction.atomic():
        SportAssociation.objects.select_for_update().get(pk=association.pk)
        changed = association_templates(association).filter(pk=template_id).update(
            archived=True, enabled=False, is_default=False,
            updated_by_id=request.original_user.pk, updated_at=timezone.now(),
        )
        if not changed:
            raise NotFound()
    return Response(status=204)


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAssociationUser])
@parser_classes([JSONParser])
def message_template_sample(request):
    association_for(request)
    template = validate_template(request_data(request))
    return Response({'data': rendered_message(template, SAMPLE)})


def recipient_for(association, associate_id):
    return Associate.objects.filter(sport_association=association, pk=associate_id, deleted=False).first()


def recipient_name(recipient):
    return ' '.join((recipient.first_name or '', recipient.last_name or '')).strip()


def address_for(recipient, channel):
    if channel == 'email':
        email = (recipient.email or '').strip()
        try:
            validate_email(email)
            if len(email) <= 254 and not re.search(r'[,\s]', email):
                return email
        except DjangoValidationError:
            pass
        return ''
    phone = re.sub(r'[ ().-]', '', (recipient.phone or '').strip())
    if phone.startswith('00'):
        phone = '+' + phone[2:]
    return phone if re.fullmatch(r'\+[1-9][0-9]{6,14}', phone) else ''


def deliver_email(attempt, association):
    configuration = CommunicationConfiguration.objects.filter(sport_association=association).first()
    if configuration is None:
        configuration = CommunicationConfiguration(sport_association=association)
    accepted, _ = configuration.send_email(attempt.subject, attempt.body, [attempt.recipient])
    return accepted


# Adapters accept the persisted attempt, including its stable ID for provider deduplication.
DELIVERY = {'email': deliver_email}


def outgoing_data(attempt):
    direct = attempt.channel in DELIVERY
    result = {
        'id': str(attempt.pk), 'channel': attempt.channel, 'locale': attempt.locale,
        'template_id': str(attempt.template_id), 'recipient': attempt.recipient,
        'subject': attempt.subject, 'body': attempt.body, 'status': attempt.status,
        'can_send': direct, 'can_open_whatsapp': attempt.channel == 'whatsapp' and not direct,
        'warning': WARNINGS.get(attempt.status, ''),
    }
    if attempt.status == 'manual':
        result['whatsapp_url'] = f'https://wa.me/{attempt.recipient[1:]}?text={quote(attempt.body, safe="")}'
    return result


@extend_schema(methods=['GET'], responses=OpenApiTypes.OBJECT)
@extend_schema(methods=['POST'], request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT})
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsAssociationUser])
@parser_classes([JSONParser])
@transaction.atomic
def outgoing_message_recipient(request, associate_id):
    association = outgoing_association(request)
    recipient = recipient_for(association, identifier(associate_id))
    if recipient is None:
        raise NotFound()
    templates = association_templates(association).filter(enabled=True)
    name = recipient_name(recipient)
    if request.method == 'GET':
        return Response({'data': {
            'recipient_name': name,
            'templates': [template_data(row) for row in templates.order_by('channel', 'locale', 'id')],
        }})

    data = request_data(request)
    channel = channel_code(data)
    locale = locale_code(data)
    raw_id = data.get('template_id')
    template_id = identifier(raw_id) if raw_id not in (None, '') else None
    template = choose_template(templates, channel, locale, template_id)
    if template is None:
        invalid('template_id', 'Nessun modello attivo per il canale e la lingua.')
    address = address_for(recipient, channel)
    if not address:
        invalid('recipient', 'Il destinatario non ha un recapito valido per questo canale.')
    variables = {**message_context(data), 'customer_name': name, 'association_name': association.denomination}
    rendered = rendered_message(template_data(template), variables)
    attempt = OutgoingMessage.objects.create(
        sport_association=association, actor_id=request.original_user.pk, recipient_id=recipient.pk,
        template_id=template.pk, channel=channel, locale=template.locale, recipient=address,
        rendered_subject=rendered['subject'], rendered_body=rendered['body'], **rendered,
    )
    return Response({'data': outgoing_data(attempt)}, status=201)


@extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT})
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAssociationUser])
@parser_classes([JSONParser])
def outgoing_message_action(request, preview_id):
    association = outgoing_association(request)
    attempts = OutgoingMessage.objects.filter(sport_association=association, actor_id=request.original_user.pk)
    attempt = attempts.filter(pk=identifier(preview_id)).first()
    if attempt is None:
        raise NotFound()

    data = request_data(request)
    action = data.get('action')
    delivery = DELIVERY.get(attempt.channel)
    if action not in ('send', 'discard', 'open_whatsapp'):
        invalid('action', 'Azione non valida.')
    edited = message_text(data, attempt.channel, required=action != 'discard')
    if attempt.status == 'preview':
        if action == 'discard':
            new_status = 'discarded'
        else:
            recipient = recipient_for(association, attempt.recipient_id)
            if recipient is None or address_for(recipient, attempt.channel) != attempt.recipient:
                return Response({'detail': 'Il recapito è cambiato. Crea una nuova anteprima.'}, status=409)
            if action == 'open_whatsapp':
                if attempt.channel != 'whatsapp' or delivery:
                    invalid('action', 'Apertura manuale di WhatsApp non disponibile.')
                new_status = 'manual'
            else:
                new_status = 'sending' if delivery else 'skipped'
        claimed = attempts.filter(pk=attempt.pk, status='preview').update(
            action=action, status=new_status, updated_at=timezone.now(), **edited,
        )
        attempt.refresh_from_db()
        if claimed and new_status == 'sending':
            try:
                accepted = delivery(attempt, association)
            except Exception:
                logger.exception('Unable to submit outgoing message %s', attempt.pk)
                accepted = False
            attempt.status = 'submitted' if accepted else 'failed'
            attempt.updated_at = timezone.now()
            attempt.save(update_fields=['status', 'updated_at'])

    if attempt.action != action or attempt.subject != edited['subject'] or attempt.body != edited['body']:
        return Response({'detail': 'Questa anteprima è già stata utilizzata.'}, status=409)
    return Response({'data': outgoing_data(attempt)})
