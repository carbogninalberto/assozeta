"""Explicit, revocable impersonation using the existing authenticated administrator."""
from uuid import UUID, uuid4
import re

from auditlog.context import auditlog_value
from auditlog.models import LogEntry
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import PermissionDenied

from application.models import User, SportAssociation

SESSION_TTL = 3600


def active_admin(user):
    return bool(user and user.is_authenticated and user.is_active and not user.deleted and user.is_superuser)


def session_key(identifier):
    try:
        return f'impersonation:v1:{UUID(str(identifier))}'
    except (ValueError, TypeError, AttributeError):
        raise PermissionDenied('Sessione di impersonificazione non valida. Torna all’amministrazione.') from None


def target_user(identifier):
    try:
        target = User.objects.select_related('connected_user').get(pk=identifier, is_active=True, is_superuser=False)
        if target.is_collaborator:
            owner = target.connected_user
            if not owner or not owner.is_active or owner.deleted or owner.role != User.ASSOCIATION:
                raise User.DoesNotExist()
            SportAssociation.objects.get(user=owner)
        elif target.role == User.ASSOCIATION:
            SportAssociation.objects.get(user=target)
        return target
    except (User.DoesNotExist, SportAssociation.DoesNotExist, DjangoValidationError, ValueError, TypeError):
        raise PermissionDenied('Utente non disponibile per l’impersonificazione.') from None


def resolve_target(actor, identifier, target_id=None):
    if not active_admin(actor):
        raise PermissionDenied('Solo un amministratore può impersonificare utenti.')
    record = cache.get(session_key(identifier))
    if not record or record['actor_id'] != str(actor.pk) or (target_id and record['target_id'] != str(target_id)):
        raise PermissionDenied('Sessione di impersonificazione scaduta o revocata. Torna all’amministrazione.')
    return target_user(record['target_id'])


def audit_transition(actor, target, identifier, action):
    LogEntry.objects.log_create(target, action=LogEntry.Action.ACCESS, actor=actor,
        changes={'impersonation': ['', action]}, additional_data={
            'impersonation_session': identifier, 'administrator_id': str(actor.pk),
            'target_user_id': str(target.pk), 'target_role': target.role,
        }, force_log=True)


def begin(actor, target_id):
    if not active_admin(actor):
        raise PermissionDenied('Solo un amministratore può impersonificare utenti.')
    target = target_user(target_id)
    identifier = str(uuid4())
    audit_transition(actor, target, identifier, 'started')
    cache.set(session_key(identifier), {'actor_id': str(actor.pk), 'target_id': str(target.pk)}, SESSION_TTL)
    return identifier, target


def end(actor, identifier):
    if not active_admin(actor):
        raise PermissionDenied('Solo un amministratore può terminare l’impersonificazione.')
    key = session_key(identifier)
    record = cache.get(key)
    if not record:
        return
    if record['actor_id'] != str(actor.pk):
        raise PermissionDenied('Sessione di un altro amministratore.')
    target = User.original_objects.filter(pk=record['target_id']).first()
    if target:
        audit_transition(actor, target, str(identifier), 'ended')
    cache.delete(key)


def resolve_request_identity(request, actor=None):
    if getattr(request, '_identity_resolved', False):
        return request.user
    actor = actor if actor is not None else request.user
    request.original_user = actor
    request.authenticated_user = actor
    request.collaborator = False
    request.impersonating = False
    path = request.path.strip('/').removeprefix('api/')
    administration = path.startswith(('instance/', 'administration/'))
    target = actor
    if not administration and (request.headers.get('User-Id') or request.headers.get('X-Impersonation-Id')):
        target = resolve_target(actor, request.headers.get('X-Impersonation-Id'), request.headers.get('User-Id'))
        request.impersonating = True
    request.effective_user = target
    scope = target
    if target and target.is_authenticated and target.is_collaborator and not administration:
        scope = target.connected_user
        if not scope or not scope.is_active or scope.deleted:
            raise PermissionDenied('Associazione del collaboratore non disponibile.')
        request.collaborator = True
    # Administrative data exports have one explicit scope: the configured primary
    # association. Other associations require entering their normal owner context.
    if active_admin(actor) and not request.impersonating and path.startswith('association/export/'):
        from instance.models import InstanceConfiguration
        config = InstanceConfiguration.get_config()
        if not config or not config.self_hosted or not config.primary_association_id:
            raise PermissionDenied('Nessuna associazione principale configurata per l’export.')
        scope = config.primary_association.user
    if active_admin(actor) and not request.impersonating and not administration and not (
        path == 'profile/info' or path == 'sport-associations/list' or
        re.fullmatch(r'sport-associations/[0-9a-fA-F-]{36}/admin-update', path) or path.startswith(('association/export/', 'oauth2/', 'notifications/'))
    ):
        raise PermissionDenied('Seleziona un utente da impersonificare per accedere a questa sezione.')
    request._identity_resolved = True
    request.user = scope
    if request.collaborator:
        from application.permissions_registry import check_collaborator_permission
        check_collaborator_permission(request)
    # Retain the initiating administrator as the audit actor, including when a
    # collaborator's tenant owner is used to scope business data.
    try:
        auditlog_value.get()['actor'] = actor if actor and actor.is_authenticated else None
    except LookupError:
        pass
    return scope


def acting_user(request):
    """Return the selected person before tenant scoping, including legacy callers."""
    return vars(request).get('effective_user', getattr(request, 'original_user', request.user))
