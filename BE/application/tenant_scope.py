"""Request-scoped tenant checks for writable related fields.

Identity resolution records the scope user for the current request. Every
ModelSerializer related field then rejects objects that resolve (through the
audit association resolvers) to a different sport association with DRF's
generic permission denial, matching views that already refuse foreign
references with 403. Outside a request (tasks, commands) nothing is enforced.
"""
from asgiref.local import Local
from django.contrib.contenttypes.models import ContentType
from rest_framework import relations
from rest_framework.exceptions import PermissionDenied

_local = Local()

_UNSET = object()


def set_scope_user(user):
    _local.tenant_scope_user = user
    _local.tenant_scope_association = _UNSET


def clear_scope():
    for name in ('tenant_scope_user', 'tenant_scope_association'):
        try:
            delattr(_local, name)
        except AttributeError:
            pass


def current_association():
    user = getattr(_local, 'tenant_scope_user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    association = getattr(_local, 'tenant_scope_association', _UNSET)
    if association is _UNSET:
        from application.models import SportAssociation
        association = SportAssociation.objects.filter(user=user).first()
        _local.tenant_scope_association = association
    return association


def resolve_association(obj):
    from application.audit_resolvers import get_resolver
    from application.models import SportAssociation
    if isinstance(obj, SportAssociation):
        return obj
    resolver = get_resolver(ContentType.objects.get_for_model(obj, for_concrete_model=True))
    resolved = resolver(obj) if resolver else None
    if resolved is None and getattr(obj, 'sport_association_id', None):
        return obj.sport_association
    return resolved[0] if resolved else None


def belongs_to_current_tenant(obj):
    association = current_association()
    if association is None:
        return True
    owner = resolve_association(obj)
    return owner is None or owner.pk == association.pk


class TenantScopedPrimaryKeyRelatedField(relations.PrimaryKeyRelatedField):
    def to_internal_value(self, data):
        obj = super().to_internal_value(data)
        if not belongs_to_current_tenant(obj):
            raise PermissionDenied()
        return obj


def install():
    """Record each resolved identity and validate every serializer relation."""
    from django.core.signals import request_started
    from rest_framework import serializers
    from application import impersonation

    resolve = impersonation.resolve_request_identity
    if not getattr(resolve, 'tenant_scoped', False):
        def resolve_request_identity(request, actor=None):
            user = resolve(request, actor)
            set_scope_user(user)
            return user
        resolve_request_identity.tenant_scoped = True
        impersonation.resolve_request_identity = resolve_request_identity
        # core.authentication binds the name at import time; rebind it too.
        from core import authentication
        authentication.resolve_request_identity = resolve_request_identity
    request_started.connect(lambda **kwargs: clear_scope(), weak=False, dispatch_uid='tenant-scope-clear')
    serializers.ModelSerializer.serializer_related_field = TenantScopedPrimaryKeyRelatedField
