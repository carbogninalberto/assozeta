import logging

from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import AnonymousUser
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed

from rest_framework.decorators import permission_classes, api_view
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from application.models.invoices_models import Invoice
from application.models.user_models import SportAssociationDocumentsArchive, User
from core.middleware import IsAuthenticated
from rest_framework.response import Response

from application.models.subscriptions_models import MedicalCertificate, Subscription
from application.utils.api_utils import is_valid_uuid
from application.utils.printing import PrintingService
from core.settings import STORAGE_DIR
import os
from django.core.files.storage import default_storage

from docmanager.models import Document
from docmanager.download_tokens import load_document_download_token

logger = logging.getLogger(__name__)


def _get_authenticated_user(request, query_token=None):
    auth = JWTAuthentication()

    try:
        header = auth.get_header(request)
        if header is not None:
            raw_token = auth.get_raw_token(header)
            if raw_token is not None:
                validated_token = auth.get_validated_token(raw_token)
                return auth.get_user(validated_token)
    except (AuthenticationFailed, InvalidToken, TokenError):
        pass

    cookie_token = request.COOKIES.get('BKN_AUTH')
    if cookie_token:
        try:
            validated_token = auth.get_validated_token(cookie_token)
            return auth.get_user(validated_token)
        except (AuthenticationFailed, InvalidToken, TokenError):
            pass

    if query_token:
        try:
            validated_token = auth.get_validated_token(query_token)
            return auth.get_user(validated_token)
        except (AuthenticationFailed, InvalidToken, TokenError):
            pass

    return AnonymousUser()


def _get_document_from_related_uid(uid):
    try:
        return Document.objects.get(document_id=uid)
    except ObjectDoesNotExist:
        pass

    subscription = Subscription.objects.filter(subscription_id=uid).select_related('document_pdf').first()
    if subscription and subscription.document_pdf:
        return subscription.document_pdf

    medical = MedicalCertificate.objects.filter(medical_id=uid).select_related('document').first()
    if medical and medical.document:
        return medical.document

    invoice = Invoice.objects.filter(invoice_id=uid).select_related('document_pdf').first()
    if invoice and invoice.document_pdf:
        return invoice.document_pdf

    return None


# authentication is done in the code
@api_view(['GET'])
@xframe_options_exempt
def retrieve_document(request, uid):
    is_valid_uuid(uid)

    download = request.GET.get('download', True)

    if download == 'false':
        download = False
    else:
        download = True

    # template = os.path.join(BASE_DIR, 'templates/document/application/subscription.html')
    logger.info('retrieve_document {}'.format(uid))
    token = request.GET.get("token", None)
    download_token = request.GET.get("download_token", None)
    request.user = _get_authenticated_user(request, query_token=token)

    export_archive = None
    if download_token:
        token_payload = load_document_download_token(download_token)
        if token_payload is None or token_payload['document_id'] != str(uid):
            return Response({'error': 'Invalid download token.'}, status.HTTP_401_UNAUTHORIZED)
        export_archive = SportAssociationDocumentsArchive.objects.filter(
            sport_association_id=token_payload['sport_association_id'],
            document_id=uid,
            document__filename__startswith='export_',
        ).select_related('document', 'sport_association').first()
        if export_archive is None:
            return Response({'error': 'Document not found.'}, status.HTTP_404_NOT_FOUND)
        document = export_archive.document
    else:
        document = None

    # Check if token matches the document's external access token (UUID)
    if document is None and token:
        try:
            # Only try UUID lookup if token looks like a valid UUID
            document = Document.objects.filter(document_id=uid, token=token).first()
        except Exception:
            # Token is not a valid UUID (e.g., JWT token), skip this lookup
            pass

    if document is None:
        # Support authenticated access via document ID and legacy related-resource IDs.
        if request.user and request.user.is_authenticated:
            document = _get_document_from_related_uid(uid)
            if document is None:
                return Response({'error': 'Document not found.'}, status.HTTP_404_NOT_FOUND)
        else:
            return Response({'error': 'Invalid token. Not Authorized.'}, status.HTTP_401_UNAUTHORIZED)

    if export_archive is None and document.filename.startswith('export_'):
        export_archive = SportAssociationDocumentsArchive.objects.filter(
            document=document,
        ).select_related('sport_association').first()
        if export_archive is not None:
            is_owner = (
                request.user
                and request.user.is_authenticated
                and request.user.role == User.ASSOCIATION
                and request.user.user_id == export_archive.sport_association.user_id
            )
            if not is_owner:
                return Response(
                    {'error': 'Not authorized to access this export.'},
                    status.HTTP_403_FORBIDDEN,
                )

    printing_service = PrintingService()

    if download:
        response = printing_service.download_file(request, document=document, token=token)
        return response
    else:
        response = printing_service.view_file(request, document=document, token=token)
        return response


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_document(request, uid):
    is_valid_uuid(uid)
    # TODO: add permission check
    try:
        document = Document.objects.get(document_id=uid)
        document.delete()
        return Response({'msg': 'success!'}, status=status.HTTP_200_OK)
    except ObjectDoesNotExist as e:
        return Response({'error': 'Document not found.'}, status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
# @permission_classes([IsAuthenticated])
def medical_certificate_document(request):


    logger.info('medical_certificate_document')

    data = request.data

    if 'medical_certificate' in data.keys():
        medical_certificate = data['medical_certificate']
        logger.info(medical_certificate)
        # template = os.path.join(BASE_DIR, 'templates/document/application/subscription.html')

        document = Document.objects.create(filename=medical_certificate.name)
        document.save()

        storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
        file = os.path.join(storing_path, document.filename)

        default_storage.save(file, medical_certificate.file)

        output_dict = {
            'expiring_date': None
        }

        usr = request.user if request.user.is_authenticated else None
        medical_certificate_doc = MedicalCertificate.objects.create(document=document, user=usr)
        medical_certificate_doc.save()
        return Response({
            'msg': 'success!',
            'uid': medical_certificate_doc.medical_id,
            'expiring_date': output_dict['expiring_date']
        }, status=status.HTTP_200_OK)
    else:
        raise TypeError("medical_certificate key not present!")
