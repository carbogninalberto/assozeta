"""Staff-board ownership, rich-document validation and realtime invalidation."""
import json
import logging
import re
from urllib.parse import urlsplit

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from rest_framework import serializers

logger = logging.getLogger(__name__)
MAX_DOCUMENT_BYTES = 14 * 1024 * 1024  # Includes the editor's base64 image data.
NODE_TYPES = {'doc', 'paragraph', 'heading', 'text', 'hardBreak', 'bulletList', 'orderedList',
              'listItem', 'blockquote', 'codeBlock', 'horizontalRule', 'image', 'taskList',
              'taskItem', 'table', 'tableRow', 'tableCell', 'tableHeader', 'pageBreak'}
MARK_TYPES = {'bold', 'italic', 'underline', 'strike', 'code', 'link'}


def message_actor(request):
    if getattr(request, 'collaborator', False) and getattr(request, 'original_user', None):
        return request.original_user
    return request.user


def safe_url(value, image=False):
    if not isinstance(value, str) or re.search(r'[\x00-\x20\x7f]', value):
        return False
    if image and re.fullmatch(r'data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}', value):
        return True
    try:
        url = urlsplit(value)
        return (url.scheme in ('http', 'https') and bool(url.netloc)) or (not image and url.scheme in ('mailto', 'tel'))
    except ValueError:
        return False


def validate_document(document):
    """Accept the editor schema only; never accept executable HTML or arbitrary attributes."""
    if not isinstance(document, dict) or document.get('type') != 'doc':
        raise serializers.ValidationError('Documento non valido.')
    if len(json.dumps(document).encode()) > MAX_DOCUMENT_BYTES:
        raise serializers.ValidationError('Il messaggio con le immagini è troppo grande (massimo 14 MB).')
    text_parts, images = [], []
    count = 0

    def visit(node, depth=0):
        nonlocal count
        count += 1
        if depth > 30 or count > 5000 or not isinstance(node, dict) or node.get('type') not in NODE_TYPES:
            raise serializers.ValidationError('Struttura del documento non valida.')
        kind = node['type']
        attrs = node.get('attrs') or {}
        if not isinstance(attrs, dict):
            raise serializers.ValidationError('Attributi non validi.')
        result = {'type': kind}
        cleaned = {}
        if kind == 'text':
            if not isinstance(node.get('text'), str):
                raise serializers.ValidationError('Testo non valido.')
            result['text'] = node['text']
            text_parts.append(node['text'])
        if kind == 'image':
            if not safe_url(attrs.get('src'), image=True):
                raise serializers.ValidationError('Usa immagini PNG, JPEG, GIF o WebP, oppure un URL HTTP/HTTPS.')
            cleaned = {key: attrs[key] for key in ('src', 'alt', 'title') if isinstance(attrs.get(key), str)}
            images.append(cleaned['src'])
        for key in ('level', 'start', 'colspan', 'rowspan'):
            if key in attrs:
                if type(attrs[key]) is not int or not 1 <= attrs[key] <= (6 if key == 'level' else 1000):
                    raise serializers.ValidationError('Dimensione non valida.')
                cleaned[key] = attrs[key]
        if attrs.get('textAlign') in ('left', 'center', 'right', 'justify'):
            cleaned['textAlign'] = attrs['textAlign']
        if kind == 'taskItem':
            cleaned['checked'] = attrs.get('checked') is True
        if cleaned:
            result['attrs'] = cleaned
        marks = node.get('marks', [])
        if not isinstance(marks, list) or len(marks) > 10:
            raise serializers.ValidationError('Formattazione non valida.')
        if marks:
            result['marks'] = []
            for mark in marks:
                if not isinstance(mark, dict) or mark.get('type') not in MARK_TYPES:
                    raise serializers.ValidationError('Formattazione non valida.')
                clean_mark = {'type': mark['type']}
                if mark['type'] == 'link':
                    mark_attrs = mark.get('attrs') or {}
                    href = mark_attrs.get('href') if isinstance(mark_attrs, dict) else None
                    if not safe_url(href):
                        raise serializers.ValidationError('Link non valido.')
                    clean_mark['attrs'] = {'href': href, 'target': '_blank', 'rel': 'noopener noreferrer'}
                result['marks'].append(clean_mark)
        children = node.get('content', [])
        if not isinstance(children, list):
            raise serializers.ValidationError('Contenuto non valido.')
        if children:
            result['content'] = [visit(child, depth + 1) for child in children]
        if kind in ('paragraph', 'heading', 'listItem', 'hardBreak', 'codeBlock'):
            text_parts.append('\n')
        return result

    cleaned = visit(document)
    text = ''.join(text_parts).strip()
    if not text and not images:
        raise serializers.ValidationError('Scrivi un messaggio o aggiungi un’immagine.')
    if len(text) > 10000:
        raise serializers.ValidationError('Il testo può contenere al massimo 10000 caratteri.')
    return cleaned, text


def staff_board_group(association_id):
    return f'staff_board_{association_id}'


def readable_association_id(user_id):
    """Recheck current role/permissions when joining and delivering a socket event."""
    from application.models import User, BillingSubscription, BillingPlan, SportAssociation
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if not user or user.role not in (User.ASSOCIATION, User.COLLABORATOR):
        return None
    if user.role == User.COLLABORATOR:
        if not user.connected_user_id:
            return None
        if user.collaborator_role != User.FULL and 'association.communication.messages.read' not in (user.collaborator_permissions or []):
            return None
        owner_id = user.connected_user_id
    else:
        owner_id = user.pk
    if not BillingSubscription.objects.filter(user_id=owner_id, billing_plan__billing_type__in=(BillingPlan.PRO_PLAN, BillingPlan.TEAMS_PLAN)).exists():
        return None
    association_id = SportAssociation.objects.filter(user_id=owner_id, user__is_active=True).values_list('pk', flat=True).first()
    return str(association_id) if association_id else None


def publish_staff_board_change(association_id):
    """Send only an invalidation after commit; clients refetch through the permission-checked API."""
    def publish():
        try:
            layer = get_channel_layer()
            if layer:
                async_to_sync(layer.group_send)(staff_board_group(association_id), {'type': 'staff_board_changed'})
        except Exception:
            # A notification outage must not turn a committed write into an apparent failed write.
            logger.exception('Unable to publish staff-board update')
    transaction.on_commit(publish)
