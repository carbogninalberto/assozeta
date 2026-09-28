"""Explicit, revocable impersonation using the existing authenticated account."""
from uuid import UUID, uuid4
import re

from auditlog.context import auditlog_value
from auditlog.models import LogEntry
from django.core.cache import cache
from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import PermissionDenied

from application.models import User, SportAssociation, Associate

SESSION_TTL = 3600


def active_admin(user):
    return bool(user and user.is_authenticated and user.is_active and not user.deleted and user.is_superuser)


def session_key(identifier):
    try:
        return f'impersonation:v1:{UUID(str(identifier))}'
    except (ValueError, TypeError, AttributeError):
        raise PermissionDenied('Sessione di impersonificazione non valida. Torna al tuo account.') from None


def actor_association(actor):
    """Resolve the real initiator afresh, never a collaborator's tenant owner."""
    if not actor or not actor.is_authenticated:
        raise PermissionDenied('Accesso non consentito.')
    actor = User.objects.filter(pk=actor.pk, is_active=True).first()
    if not actor:
        raise PermissionDenied('Account non disponibile.')
    if active_admin(actor):
        return None
    if actor.role != User.ASSOCIATION:
        raise PermissionDenied('Solo il titolare può impersonare utenti.')
    association = SportAssociation.objects.filter(user=actor).first()
    if not association:
        raise PermissionDenied('Associazione non disponibile.')
    return association


def eligible_users(actor):
    association = actor_association(actor)
    users = User.objects.filter(is_active=True, is_superuser=False).select_related(
        'connected_user__sportassociation', 'sportassociation')
    if association:
        members = Associate.objects.all_objects().filter(
            sport_association=association, deleted=False, user__isnull=False).values('user_id')
        return users.exclude(pk=actor.pk).filter(
            Q(role=User.ATHLETE, pk__in=members) |
            Q(role=User.COLLABORATOR, connected_user_id=actor.pk))
    return users.filter(Q(role=User.ATHLETE) | Q(role=User.ASSOCIATION, sportassociation__deleted=False) |
        Q(role=User.COLLABORATOR, connected_user__is_active=True, connected_user__deleted=False,
          connected_user__role=User.ASSOCIATION, connected_user__sportassociation__deleted=False))


def target_user(actor, identifier):
    try:
        return eligible_users(actor).get(pk=identifier)
    except (User.DoesNotExist, DjangoValidationError, ValueError, TypeError):
        raise PermissionDenied('Utente non disponibile per l’impersonificazione.') from None


def resolve_target(actor, identifier, target_id=None):
    association = actor_association(actor)
    record = cache.get(session_key(identifier))
    association_id = str(association.pk) if association else None
    if (not record or record['actor_id'] != str(actor.pk) or
            record.get('association_id') != association_id or
            (target_id and record['target_id'] != str(target_id))):
        raise PermissionDenied('Sessione di impersonificazione scaduta o revocata. Torna al tuo account.')
    target = target_user(actor, record['target_id'])
    target.impersonation_association = association
    return target


def audit_transition(actor, target, identifier, action, association_id=None):
    LogEntry.objects.log_create(target, action=LogEntry.Action.ACCESS, actor=actor,
        changes={'impersonation': ['', action]}, additional_data={
            'impersonation_session': identifier, 'actor_id': str(actor.pk),
            'administrator_id': str(actor.pk) if active_admin(actor) else None,
            'association_id': association_id,
            'target_user_id': str(target.pk), 'target_role': target.role,
        }, force_log=True)


def begin(actor, target_id):
    association = actor_association(actor)
    target = target_user(actor, target_id)
    identifier = str(uuid4())
    audit_transition(actor, target, identifier, 'started', str(association.pk) if association else None)
    cache.set(session_key(identifier), {'actor_id': str(actor.pk), 'target_id': str(target.pk),
        'association_id': str(association.pk) if association else None}, SESSION_TTL)
    return identifier, target


def end(actor, identifier):
    # A revoked owner can still discard their own session. Starting or using a
    # session continues to require current eligibility.
    if not actor or not actor.is_authenticated:
        raise PermissionDenied('Accesso non consentito.')
    key = session_key(identifier)
    record = cache.get(key)
    if not record:
        return
    if record['actor_id'] != str(actor.pk):
        raise PermissionDenied('Sessione di un altro account.')
    target = User.original_objects.filter(pk=record['target_id']).first()
    if target:
        audit_transition(actor, target, str(identifier), 'ended', record.get('association_id'))
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
    identity_control = path in ('association/impersonation', 'association/impersonation/users')
    target = actor
    if administration and not active_admin(actor) and request.headers.get('X-Impersonation-Id'):
        raise PermissionDenied('Esci dall’impersonificazione per accedere all’amministrazione.')
    if not administration and not identity_control and (request.headers.get('User-Id') or request.headers.get('X-Impersonation-Id')):
        try:
            target = resolve_target(actor, request.headers.get('X-Impersonation-Id'), request.headers.get('User-Id'))
        except PermissionDenied as failure:
            raise PermissionDenied({'detail': str(failure.detail), 'impersonation_invalid': True}) from failure
        request.impersonating = True
    request.impersonation_association = getattr(target, 'impersonation_association', None)
    request.effective_user = target
    if request.impersonation_association:
        from application.impersonation_scope import enforce_scoped_request
        enforce_scoped_request(request)
    scope = target
    if target and target.is_authenticated and target.is_collaborator and not administration and not identity_control:
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
    # Retain the initiating account as the audit actor, including when a
    # collaborator's tenant owner is used to scope business data.
    try:
        auditlog_value.get()['actor'] = actor if actor and actor.is_authenticated else None
        if request.impersonating:
            auditlog_value.get()['additional_data'] = {
                'impersonation_session': request.headers.get('X-Impersonation-Id'),
                'actor_id': str(actor.pk), 'target_user_id': str(target.pk),
                'association_id': str(request.impersonation_association.pk) if request.impersonation_association else None,
            }
    except LookupError:
        pass
    return scope


def acting_user(request):
    """Return the selected person before tenant scoping, including legacy callers."""
    return vars(request).get('effective_user', getattr(request, 'original_user', request.user))
