"""Validate global calendar mutations before changing events or reminders."""
from datetime import datetime, timezone
from uuid import UUID

from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound

from application.models import User


def can_manage_events(request, action):
    user = getattr(request, 'original_user', request.user)
    return (not user.is_collaborator or user.collaborator_role == User.FULL or
            f'association.events.{action}' in (user.collaborator_permissions or []))


def event_datetime(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError, AttributeError):
        raise ValidationError('Invalid calendar date.')


def mutate_events(request, existing):
    data = request.data
    action = data.get('action')
    incoming = data.get('events', [])
    if not isinstance(incoming, list) or any(not isinstance(e, dict) for e in incoming):
        raise ValidationError('Events must be a list of objects.')
    ids = set()
    for event in incoming:
        try:
            UUID(event['event_id'])
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValidationError('Invalid event ID.')
        if event['event_id'] in ids:
            raise ValidationError('Duplicate event ID.')
        ids.add(event['event_id'])
        if not isinstance(event.get('title'), str) or not event['title'].strip():
            raise ValidationError('Event title is required.')
        start = event_datetime(event.get('start'))
        if event.get('end') and event_datetime(event['end']) < start:
            raise ValidationError('Event end precedes start.')
        props = event.get('extendedProps', {})
        if not isinstance(props, dict) or props.get('course'):
            raise ValidationError('Invalid global event properties.')
        if props.get('reminder_enabled'):
            from application.utils.api_utils import REMINDER_UNITS_MAP_TEXT
            raw_amount = props.get('reminder_amount')
            try:
                amount = int(raw_amount)
            except (TypeError, ValueError):
                raise ValidationError('Invalid reminder amount.')
            if props.get('reminder_unit') not in REMINDER_UNITS_MAP_TEXT or \
                    isinstance(raw_amount, bool) or not 0 <= amount <= 525600:
                raise ValidationError('Invalid reminder settings.')

    old = {e['event_id']: e for e in existing}
    if action in ('create', 'update', 'delete'):
        if not can_manage_events(request, action):
            raise PermissionDenied('Missing event permission.')
        if action == 'delete':
            event_id = data.get('event_id')
            if not isinstance(event_id, str) or not event_id:
                raise ValidationError('Event ID is required.')
            old.pop(event_id, None)
        else:
            if len(incoming) != 1:
                raise ValidationError('Exactly one event is required.')
            event = incoming[0]
            exists = event['event_id'] in old
            if action == 'create' and exists:
                raise ValidationError('Event already exists.')
            if action == 'update' and not exists:
                raise NotFound('Event not found.')
            old[event['event_id']] = event
        return list(old.values())
    if action is not None or 'events' not in data:
        raise ValidationError('Invalid calendar action.')
    # Legacy clients send a complete snapshot. Require every permission that
    # the actual diff needs, including deletion of omitted events.
    new = {e['event_id']: e for e in incoming}
    required = set()
    if new.keys() - old.keys():
        required.add('create')
    if old.keys() - new.keys():
        required.add('delete')
    if any(new[k] != old[k] for k in new.keys() & old.keys()):
        required.add('update')
    for permission in required or {'update'}:
        if not can_manage_events(request, permission):
            raise PermissionDenied('Missing event permission.')
    return incoming
