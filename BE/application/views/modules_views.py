"""
@ copyright: Bakney srl
"""
import base64
import datetime
import os
import uuid

import pandas
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from application.models import Module, ModuleResponses
from application.serializers.modules_serializers import ModulesSerializer, ModulesSerializerWithResponses, \
    ModuleResponseSerializer, ModulesAddSerializer
from core.middleware import IsAuthenticated

import logging

from application.utils.api_utils import is_valid_uuid

from core.settings import STORAGE_DIR
from docmanager.models import Document
import io

logger = logging.getLogger(__name__)


def update_queue(module):
    all_responses = ModuleResponses.objects.filter(
        module=module,
        queue_position__isnull=False
    ).order_by('queue_position')

    # set the approved to queue_position null
    for response in all_responses.filter(approved=True):
        response.queue_position = None
        response.save()

    for i, response in enumerate(all_responses.filter(approved=False)):
        response.queue_position = i + 1
        response.save()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
# @cache_endpoint('modules_list', timeout=60 * 60 * 24 * 7)
def modules_list(request):

    user = request.user

    modules = Module.objects.filter(sport_association=user.sport_association)

    data = ModulesSerializer(modules, many=True).data

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def modules_add(request):

    user = request.user
    logger.info("Creating new module", extra={'user_id': str(user.user_id), 'sport_association_id': str(user.sport_association.sport_association_id)})

    request.data['sport_association'] = user.sport_association.sport_association_id

    serializer = ModulesAddSerializer(data=request.data)

    if serializer.is_valid(raise_exception=True):
        module = serializer.save()
        logger.info("Module created successfully", extra={'user_id': str(user.user_id), 'module_id': str(module.module_id), 'title': module.title})
        return Response({'msg': 'module created'}, status=status.HTTP_200_OK)

    logger.warning("Module creation validation failed", extra={'user_id': str(user.user_id), 'errors': str(serializer.errors)})
    return Response({'msg': 'module not created'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def modules_update(request, module_id):

    is_valid_uuid(module_id)

    if request.user.is_sport_association is False:
        logger.warning("Non-association user attempted module update", extra={'user_id': str(request.user.user_id), 'module_id': module_id})
        return Response({"error": "Only sport associations can update camps or retreats"},
                        status=status.HTTP_403_FORBIDDEN)

    try:
        logger.info("Updating module", extra={'user_id': str(request.user.user_id), 'module_id': module_id})
        module = Module.objects.get(module_id=module_id)

        logger.debug("Checking module ownership", extra={'module_id': module_id, 'module_sport_association': str(module.sport_association.sport_association_id), 'user_sport_association': str(request.user.sport_association.sport_association_id)})
        if module.sport_association != request.user.sport_association:
            logger.warning("Unauthorized module update attempt", extra={'user_id': str(request.user.user_id), 'module_id': module_id})
            raise PermissionDenied("You are not allowed to update this camp or retreat")

        serializer = ModulesSerializer(module, data=request.data, partial=True)

        if serializer.is_valid(raise_exception=True):
            serializer.save()

        logger.info("Module updated successfully", extra={'module_id': module_id, 'title': module.title})
        return Response({
            "message": "Camp or retreat updated successfully",
            "camp_and_retreat": serializer.data
        }, status=status.HTTP_200_OK)
    except Module.DoesNotExist:
        logger.error("Module not found for update", extra={'module_id': module_id}, exc_info=True)
        return Response({"error": "Camp or retreat not found"}, status=status.HTTP_404_NOT_FOUND)
    except ValidationError as e:
        logger.warning("Module update validation failed", extra={'module_id': module_id, 'error': str(e)})
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error("Module update failed", extra={'module_id': module_id, 'error': str(e)}, exc_info=True)
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def modules_delete(request, module_id):

    user = request.user
    logger.info("Deleting module", extra={'user_id': str(user.user_id), 'module_id': module_id})

    module = Module.objects.filter(module_id=module_id, sport_association=user.sport_association).first()

    if not module:
        logger.warning("Module not found for deletion", extra={'user_id': str(user.user_id), 'module_id': module_id})
        return Response({'error': 'module not found'}, status=status.HTTP_404_NOT_FOUND)

    module.delete()
    logger.info("Module deleted successfully", extra={'module_id': module_id, 'title': module.title})

    return Response({'msg': 'module deleted'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def modules_check_link(request):
    data = request.query_params

    # get module_id by header request: Module
    module_id = request.headers.get('module', None)

    if len(data['custom_link']) > 255:
        return Response({"valid": False, "exception": "invalid payload."}, status=status.HTTP_400_BAD_REQUEST)

    modules = Module.objects.filter(
        custom_link=data['custom_link']
    )
    if len(modules) > 0:
        if module_id is not None and str(modules.first().module_id) == module_id:
            return Response({"valid": True}, status=status.HTTP_200_OK)
        return Response({"valid": False, "exception": "invalid payload."}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"valid": True}, status=status.HTTP_200_OK)


@api_view(['GET'])
def modules_custom_link_info(request, custom_link):

    # get the module by the custom link
    module = Module.objects.filter(custom_link=custom_link).first()
    if not module:
        return Response({'error': 'module not found'}, status=status.HTTP_404_NOT_FOUND)

    response_data = None

    # check if the is response_id in the query params
    response_id = request.query_params.get('response_id', None)
    if response_id is not None and is_valid_uuid(response_id):
        response = ModuleResponses.objects.filter(module_response_id=response_id).first()
        response_data = ModuleResponseSerializer(response).data

    # check if module is active
    if not module.enabled:
        return Response({'error': 'module not active'}, status=status.HTTP_404_NOT_FOUND)

    return Response({'data': ModulesSerializer(module).data, 'response_data': response_data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def modules_overview(request, module_id):

    module = Module.objects.filter(
        module_id=module_id,
        sport_association=request.user.sport_association
    ).first()

    if not module:
        return Response({'error': 'module not found'}, status=status.HTTP_404_NOT_FOUND)

    return Response({'data': ModulesSerializerWithResponses(module).data}, status=status.HTTP_200_OK)


@api_view(['POST'])
def modules_response_add(request, module_id):

    is_valid_uuid(module_id)
    logger.info("Adding module response", extra={'module_id': module_id, 'user_authenticated': request.user.is_authenticated if hasattr(request, 'user') else False})

    module = Module.objects.filter(module_id=module_id).first()
    if not module:
        logger.warning("Module not found for response", extra={'module_id': module_id})
        return Response({'error': 'module not found'}, status=status.HTTP_404_NOT_FOUND)

    logger.debug("Checking module status", extra={'module_id': module_id, 'enabled': module.enabled, 'max_responses_reached': module.is_max_responses_reached, 'queue_mode': module.queue_mode})
    if not module.enabled:
        logger.warning("Module not enabled for responses", extra={'module_id': module_id})
        return Response({'error': 'module not enabled'}, status=status.HTTP_404_NOT_FOUND)

    if module.is_max_responses_reached and not module.queue_mode:
        logger.warning("Module max responses reached", extra={'module_id': module_id})
        return Response({'error': 'max responses reached'}, status=status.HTTP_404_NOT_FOUND)

    if module.only_users:
        if not request.user.is_authenticated:
            return Response({'error': 'only users'}, status=status.HTTP_404_NOT_FOUND)

    if module.payment_required:
        if request.user is not None:
            user = request.user
        else:
            user = None
        # TODO: create payment

    # get last response
    last_response = ModuleResponses.objects.filter(
        module=module
    ).order_by('-progressive_response_number').first()

    if last_response is None:
        progressive_response_number = 0
    else:
        progressive_response_number = last_response.progressive_response_number + 1

    queue_position = None
    if module.queue_mode:
        last_queue_position = ModuleResponses.objects.filter(
            module=module,
            queue_position__isnull=False
        ).order_by('-queue_position').first()

        if last_queue_position is None:
            queue_position = 1
        else:
            queue_position = last_queue_position.queue_position + 1

    module_response = ModuleResponses.objects.create(
        module=module,
        response=request.data,
        payment=None,
        approved=not module.require_approval,
        progressive_response_number=progressive_response_number,
        queue_position=queue_position
    )

    if request.user and request.user.is_authenticated:
        module_response.user = request.user
        module_response.save()

    # update the queue position
    if module.queue_mode:
        update_queue(module)

    logger.info("Module response added successfully", extra={'module_id': module_id, 'module_response_id': str(module_response.module_response_id), 'queue_position': queue_position, 'requires_approval': module.require_approval})
    return Response({'msg': 'response added', 'module_response_id': module_response.module_response_id}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def modules_response_approve(request, module_response_id):

    is_valid_uuid(module_response_id)
    logger.info("Approving module response", extra={'user_id': str(request.user.user_id), 'module_response_id': module_response_id})

    module_response = ModuleResponses.objects.filter(
        module_response_id=module_response_id,
        module__sport_association=request.user.sport_association
    ).first()

    if not module_response:
        logger.warning("Module response not found for approval", extra={'module_response_id': module_response_id})
        return Response({'error': 'response not found'}, status=status.HTTP_404_NOT_FOUND)

    module_response.approved = True
    module_response.save()

    # update the queue position
    if module_response.module.queue_mode:
        logger.debug("Updating queue positions", extra={'module_id': str(module_response.module.module_id)})
        update_queue(module_response.module)

    logger.info("Module response approved successfully", extra={'module_response_id': module_response_id, 'module_id': str(module_response.module.module_id)})
    return Response({'msg': 'response approved'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def modules_response_delete(request, module_response_id):

    is_valid_uuid(module_response_id)

    module_response = ModuleResponses.objects.filter(
        module_response_id=module_response_id,
        module__sport_association=request.user.sport_association
    ).first()

    if not module_response:
        return Response({'error': 'response not found'}, status=status.HTTP_404_NOT_FOUND)

    module = module_response.module
    module_response.delete()

    # update the queue position
    if module.queue_mode:
        update_queue(module)

    return Response({'msg': 'response deleted'}, status=status.HTTP_200_OK)


@api_view(['POST', 'DELETE'])
def modules_response_add_attachment(request, module_response_id):

    is_valid_uuid(module_response_id)

    module_response = ModuleResponses.objects.filter(
        module_response_id=module_response_id
    ).first()

    # if DELETE
    if request.method == 'DELETE':
        # get document_id from request
        if 'document_id' in request.data:
            document = Document.objects.filter(document_id=request.data['document_id']).first()
            if document:
                # remove the document from the module response
                module_response.attachments.remove(document)
                document.delete()
                return Response({'msg': 'attachment deleted'}, status=status.HTTP_200_OK)
        return Response({'error': 'document not found'}, status=status.HTTP_404_NOT_FOUND)

    # contained in file
    if 'filename' in request.data and \
            'base64' in request.data:

        document = Document.objects.create(filename=request.data['filename'])
        document.save()

        storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
        file = os.path.join(storing_path, document.filename)

        def decode_base64(data):
            # Remove data URI prefix if it exists
            if ';base64,' in data:
                data = data.split(';base64,')[1]

            # Add necessary padding
            missing_padding = len(data) % 4
            if missing_padding:
                data += '=' * (4 - missing_padding)

            return base64.b64decode(data)

        default_storage.save(file, ContentFile(decode_base64(request.data['base64'])))
        module_response.attachments.add(document)
        module_response.save()

    return Response({'msg': 'attachment added'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def modules_response_export(request, module_id):

    is_valid_uuid(module_id)
    logger.info("Exporting module responses", extra={'user_id': str(request.user.user_id), 'module_id': module_id})

    module_responses = ModuleResponses.objects.filter(
        module_id=module_id,
        module__sport_association=request.user.sport_association
    )
    if not module_responses.first():
        logger.warning("No responses found for export", extra={'module_id': module_id})
        return Response({'error': 'response not found'}, status=status.HTTP_404_NOT_FOUND)

    # export excel
    cols = []
    response_cols_id = []
    image_cols = []
    for element in module_responses.first().module.elements:
        if element['type'] not in [
            'heading',
            'paragraph',
            'link'
        ]:
            cols.append(element['props']['label'])
            response_cols_id.append(element['props']['name'])
            if element['type'] in ['signature']:
                image_cols.append(element['props']['label'])
    standard_cols = [
        'Data creazione',
        'Progressivo risposta',
        'Posizione in coda',
        'Utente',
        'Pagamento',
        'Approvato',
        'Allegati'
    ]
    cols.extend(standard_cols)

    rows = []

    for r in module_responses:
        row = []
        for key in response_cols_id:
            if key in r.response.keys():
                row.append(r.response[key])
            else:
                row.append('-')
        row.extend([
            r.creation_date.strftime('%d/%m/%Y %H:%M') if r.creation_date else '-',
            r.progressive_response_number if r.progressive_response_number is not None else '-',
            r.queue_position if r.queue_position is not None else '-',
            r.user.get_full_name() if r.user else '-',
            'Pagato' if r.payment and r.payment.paid else ('Non pagato' if r.payment else '-'),
            'Approvata' if r.approved else 'Non approvata',
            ",".join([attachment.filename if attachment else '-' for attachment in r.attachments.all()])
        ])

        rows.append(row)

    data = {
        "file": ""
    }
    df = pandas.DataFrame(rows)
    df.columns = cols

    data['filename'] = "[{}] {} al modulo ({}).{}".format(
        datetime.date.today().strftime("%Y-%m-%d"),
        "Risposte",
        module_responses.first().module.title,
        'xls'
    )

    data['type'] = 'xlsx'
    f = io.BytesIO()
    writer = pandas.ExcelWriter(f, engine='xlsxwriter')
    df.to_excel(writer, sheet_name='Risposte', index=False)
    worksheet = writer.sheets['Risposte']
    for i, col in enumerate(df.columns):
        column_len = len(col) + 10
        worksheet.set_column(i, i, column_len)
    writer.close()
    data["file"] = base64.b64encode(f.getvalue())

    logger.info("Module responses exported successfully", extra={'module_id': module_id, 'response_count': len(rows), 'export_filename': data['filename']})
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def modules_duplicate(request, module_id):

    is_valid_uuid(module_id)

    source = Module.objects.filter(
        module_id=module_id,
        sport_association=request.user.sport_association
    ).first()

    if not source:
        return Response({'error': 'module not found'}, status=status.HTTP_404_NOT_FOUND)

    clone = Module.objects.create(
        sport_association=source.sport_association,
        title=f"{source.title} (copia)",
        custom_link=uuid.uuid4().hex[:12],
        require_approval=source.require_approval,
        start_date=source.start_date,
        end_date=source.end_date,
        always_active=source.always_active,
        max_responses=source.max_responses,
        queue_mode=source.queue_mode,
        only_users=source.only_users,
        payment_required=source.payment_required,
        payment_data=source.payment_data,
        response_message=source.response_message,
        allow_attachments=source.allow_attachments,
        elements=source.elements,
    )

    logger.info("Module duplicated", extra={
        'user_id': str(request.user.user_id),
        'source_module_id': str(module_id),
        'clone_module_id': str(clone.module_id),
    })

    return Response({'data': ModulesSerializer(clone).data}, status=status.HTTP_201_CREATED)





