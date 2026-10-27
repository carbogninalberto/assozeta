"""Association-scoped document deletion.

A document has no association of its own; it belongs to the objects that
reference it. Deletion is allowed only when at least one object references
the document and every referencing object resolves to the caller's
association. Anything else answers like a missing document.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

NOT_FOUND = {'error': 'Document not found.'}


def document_belongs_to(document, association):
    from application.tenant_scope import resolve_association
    if association is None:
        return False
    referenced = False
    for relation in document._meta.related_objects:
        model = relation.related_model
        for obj in model._base_manager.filter(**{relation.field.name: document}).iterator():
            referenced = True
            owner = resolve_association(obj)
            if owner is None or owner.pk != association.pk:
                return False
    return referenced


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
