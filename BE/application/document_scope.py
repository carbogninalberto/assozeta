"""Association-scoped document deletion.

A document has no association of its own; it belongs to the objects that
reference it (resolved directly, or through the objects referencing them).
Deletion is refused when any reference belongs to another association and
allowed when they all belong to the caller's. When no reference has a known
owner (for instance the owner was deleted first), the association its creation
audit entry was indexed to decides. Anything else answers like a missing
document.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

NOT_FOUND = {'error': 'Document not found.'}


def referrers(obj):
    """Every object holding a foreign key or many-to-many link to obj."""
    for relation in obj._meta.related_objects:
        yield from relation.related_model._base_manager.filter(**{relation.field.name: obj}).iterator()


def owners_of(obj, depth=1):
    """Associations owning obj: its resolver, else (one level up) its referrers'.
    None marks an owner that cannot be determined."""
    from application.tenant_scope import resolve_association
    owner = resolve_association(obj)
    if owner is not None:
        return {owner.pk}
    if depth == 0:
        return {None}
    found = set()
    for parent in referrers(obj):
        found |= owners_of(parent, depth - 1)
    return found or {None}


def document_belongs_to(document, association):
    if association is None:
        return False
    owners = set()
    for obj in referrers(document):
        owners |= owners_of(obj)
    if owners - {None, association.pk}:
        return False
    if owners == {association.pk}:
        return True
    # Unreferenced, or referenced only by objects whose owner is unknown.
    return created_for(document, association)


def created_for(document, association):
    from auditlog.models import LogEntry
    from django.contrib.contenttypes.models import ContentType
    from application.models.audit_models import AuditLogIndex
    entries = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(document, for_concrete_model=True),
                                      object_pk=str(document.pk), action=LogEntry.Action.CREATE)
    owners = set(AuditLogIndex.objects.filter(log_entry__in=entries).values_list('sport_association_id', flat=True))
    return owners == {association.pk}


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_document(request, uid):
    from application.models import SportAssociation
    from application.utils.api_utils import is_valid_uuid
    from docmanager.models import Document
    is_valid_uuid(uid)
    document = Document.objects.filter(document_id=uid).first()
    association = SportAssociation.objects.filter(user=request.user).first()
    if document is None or not document_belongs_to(document, association):
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    document.delete()
    return Response({'msg': 'success!'}, status=status.HTTP_200_OK)


def install():
    from docmanager import urls
    from docmanager.views.document_view import delete_document as unscoped
    patterns = [pattern for pattern in urls.urlpatterns if pattern.callback is unscoped]
    assert len(patterns) == 1, 'document delete route not found'
    patterns[0].callback = delete_document
