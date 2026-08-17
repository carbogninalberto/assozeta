"""
Export/Import API Views

API endpoints for association data export and import functionality.
"""
import logging
import os
import tempfile
import uuid

from celery.result import AsyncResult
from django.core.cache import cache
from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from application.models.user_models import (
    SportAssociation,
    SportAssociationDocumentsArchive,
    User,
)
from application.services.validators import ImportValidator
from application.export_progress import (
    EXPORT_ACTIVE_CACHE_TIMEOUT,
    EXPORT_TASK_CACHE_TIMEOUT,
    build_export_snapshot,
    clear_active_export,
    export_active_cache_key,
    export_task_cache_key,
    get_active_export,
    get_last_export,
)
from application.tasks import (
    EXPORT_MAX_COUNT,
    export_association_data,
    import_association_data,
)
from core.middleware import IsAuthenticated
from docmanager.download_tokens import create_document_download_token
from instance.permissions import SetupTokenOrAuthenticated

logger = logging.getLogger(__name__)

def _export_task_cache_key(task_id):
    """Backward-compatible alias for existing callers and tests."""
    return export_task_cache_key(task_id)


class AssociationExportViewSet(ViewSet):
    """
    API endpoints for association data export.

    Allows association owners to export their data for migration
    to a self-hosted instance.
    """

    permission_classes = [IsAuthenticated]

    def _check_export_permission(self, request) -> bool:
        """
        Check if user has permission to export data.

        Only association owners (not collaborators) can export.
        """
        return (
            request.user.role == User.ASSOCIATION
            and not getattr(request, 'collaborator', False)
        )

    @action(detail=False, methods=['POST'], url_path='start')
    def start_export(self, request):
        """
        Start an export task for the current association.

        POST /api/association/export/start/

        Request body is ignored; binary media is always included.

        Returns:
        {
            "task_id": "...",
            "status": "started",
            "message": "..."
        }
        """
        user = request.user

        # Check permissions
        if not self._check_export_permission(request):
            return Response(
                {'error': 'Solo il proprietario può esportare i dati'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            sport_association = user.sport_association
        except SportAssociation.DoesNotExist:
            return Response(
                {'error': 'Associazione non trovata'},
                status=status.HTTP_404_NOT_FOUND
            )

        export_count = SportAssociationDocumentsArchive.objects.filter(
            sport_association=sport_association,
            document__filename__startswith='export_',
        ).count()
        if export_count >= EXPORT_MAX_COUNT:
            return Response(
                {'error': 'Elimina un export prima di crearne uno nuovo'},
                status=status.HTTP_409_CONFLICT,
            )

        task_id = str(uuid.uuid4())
        association_id = str(sport_association.sport_association_id)
        user_id = str(user.user_id)
        active_cache_key = export_active_cache_key(association_id)
        initial_snapshot = build_export_snapshot(
            task_id=task_id,
            sport_association_id=association_id,
            user_id=user_id,
        )
        if not cache.add(
            active_cache_key,
            initial_snapshot,
            timeout=EXPORT_ACTIVE_CACHE_TIMEOUT,
        ):
            return Response(
                {'error': 'Un export è già in corso'},
                status=status.HTTP_409_CONFLICT,
            )

        task_owner_key = _export_task_cache_key(task_id)
        try:
            cache.set(
                task_owner_key,
                {
                    'sport_association_id': association_id,
                    'user_id': user_id,
                },
                timeout=EXPORT_TASK_CACHE_TIMEOUT,
            )

            # Register state before dispatch so a fast worker cannot race recovery.
            export_association_data.apply_async(
                kwargs={
                    'sport_association_id': association_id,
                    'user_id': user_id,
                },
                task_id=task_id,
            )
        except Exception:
            try:
                cache.delete_many([active_cache_key, task_owner_key])
            except Exception:
                logger.warning(
                    'Unable to roll back export cache registration',
                    extra={'task_id': task_id},
                    exc_info=True,
                )
            raise

        logger.info(
            f"Export task started",
            extra={
                'task_id': task_id,
                'sport_association_id': association_id,
                'user_id': user_id,
            }
        )

        return Response({
            'task_id': task_id,
            'status': 'started',
            'estimate': '0%',
            'progress': initial_snapshot['progress'],
            'updated_at': initial_snapshot['updated_at'],
            'message': 'Export avviato. Riceverai una email quando sarà completato.',
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=['GET'], url_path='status')
    def export_status(self, request):
        """
        Check export task status.

        GET /api/association/export/status/?task_id=<id>

        Returns:
        {
            "task_id": "...",
            "status": "PENDING|STARTED|SUCCESS|FAILURE",
            "ready": true/false,
            "result": {...}  // Only if ready
        }
        """
        if not self._check_export_permission(request):
            return Response(
                {'error': 'Solo il proprietario può visualizzare gli export'},
                status=status.HTTP_403_FORBIDDEN,
            )

        task_id = request.query_params.get('task_id')

        if not task_id:
            return Response(
                {'error': 'task_id richiesto'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            sport_association = request.user.sport_association
        except SportAssociation.DoesNotExist:
            return Response(
                {'error': 'Associazione non trovata'},
                status=status.HTTP_404_NOT_FOUND,
            )

        task_owner = cache.get(_export_task_cache_key(task_id))
        if not task_owner or task_owner.get('sport_association_id') != str(
            sport_association.sport_association_id
        ):
            return Response(
                {'error': 'Export non trovato'},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = AsyncResult(task_id)

        response_data = {
            'task_id': task_id,
            'status': result.status,
            'ready': result.ready(),
        }

        active_snapshot = get_active_export(sport_association.sport_association_id)
        if active_snapshot and active_snapshot.get('task_id') == task_id:
            response_data['status'] = active_snapshot.get('status', result.status)
            response_data['estimate'] = active_snapshot.get('estimate', '0%')
            response_data['progress'] = active_snapshot.get('progress')
            response_data['updated_at'] = active_snapshot.get('updated_at')
        elif (
            (terminal_snapshot := get_last_export(sport_association.sport_association_id))
            and terminal_snapshot.get('task_id') == task_id
        ):
            response_data['status'] = terminal_snapshot.get('status', result.status)
            response_data['ready'] = True
            response_data['estimate'] = terminal_snapshot.get('estimate', '100%')
            response_data['progress'] = terminal_snapshot.get('progress')
            response_data['updated_at'] = terminal_snapshot.get('updated_at')
        elif isinstance(result.info, dict) and result.info.get('task_id') == task_id:
            response_data['estimate'] = result.info.get('estimate', '0%')
            response_data['progress'] = result.info.get('progress')
            response_data['updated_at'] = result.info.get('updated_at')

        if result.ready():
            if result.successful():
                response_data['result'] = result.result
            else:
                response_data['error'] = str(result.result)

        return Response(response_data)

    @action(detail=False, methods=['GET'], url_path='active')
    def active_export(self, request):
        """Return the current association export snapshot for UI recovery."""
        if not self._check_export_permission(request):
            return Response(
                {'error': 'Solo il proprietario può visualizzare gli export'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            sport_association = request.user.sport_association
        except SportAssociation.DoesNotExist:
            return Response(
                {'error': 'Associazione non trovata'},
                status=status.HTTP_404_NOT_FOUND,
            )

        snapshot = get_active_export(sport_association.sport_association_id)
        if not snapshot:
            terminal = get_last_export(sport_association.sport_association_id)
            if (
                terminal
                and terminal.get('user_id') == str(request.user.user_id)
                and terminal.get('status') in {'SUCCESS', 'FAILURE'}
            ):
                return Response({'active': False, 'terminal': terminal})
            return Response({'active': False})

        if (
            snapshot.get('sport_association_id') != str(sport_association.sport_association_id)
            or snapshot.get('user_id') != str(request.user.user_id)
        ):
            return Response({'active': False})

        result = AsyncResult(snapshot['task_id'])
        if result.ready():
            clear_active_export(
                sport_association.sport_association_id,
                snapshot['task_id'],
            )
            terminal = get_last_export(sport_association.sport_association_id)
            if terminal and terminal.get('task_id') == snapshot['task_id']:
                return Response({'active': False, 'terminal': terminal})
            return Response({'active': False})

        return Response({'active': True, **snapshot})

    @action(detail=False, methods=['GET'], url_path='list')
    def list_exports(self, request):
        """
        List available export files for download.

        GET /api/association/export/list/

        Returns list of recent exports with download info.
        """
        user = request.user

        if not self._check_export_permission(request):
            return Response(
                {'error': 'Solo il proprietario può visualizzare gli export'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            sport_association = user.sport_association
        except SportAssociation.DoesNotExist:
            return Response(
                {'error': 'Associazione non trovata'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get exports (documents starting with 'export_')
        exports = SportAssociationDocumentsArchive.objects.filter(
            sport_association=sport_association,
            document__filename__startswith='export_'
        ).select_related('document').order_by('-date')[:20]

        export_list = []
        for archive in exports:
            doc = archive.document
            file_size_bytes = doc.file_size_bytes
            if file_size_bytes is None and doc.filepath:
                try:
                    file_size_bytes = default_storage.size(doc.filepath)
                    doc.file_size_bytes = file_size_bytes
                    doc.save(update_fields=['file_size_bytes'])
                except Exception:
                    logger.warning(
                        'Unable to retrieve export file size',
                        extra={'document_id': str(doc.document_id)},
                        exc_info=True,
                    )
            export_list.append({
                'archive_id': str(archive.sport_association_documents_archive_id),
                'document_id': str(doc.document_id),
                'filename': doc.filename,
                'file_size_bytes': file_size_bytes,
                'download_token': create_document_download_token(
                    doc.document_id,
                    sport_association.sport_association_id,
                ),
                'date': archive.date.isoformat() if archive.date else None,
                'created_at': doc.creation_date.isoformat() if doc.creation_date else None,
            })

        return Response({
            'exports': export_list,
            'count': len(export_list),
        })

    @action(detail=False, methods=['DELETE'], url_path='delete')
    def delete_export(self, request):
        """
        Delete an export file.

        DELETE /api/association/export/delete/

        Request body:
        {
            "document_id": "..."
        }
        """
        user = request.user
        document_id = request.data.get('document_id')

        if not self._check_export_permission(request):
            return Response(
                {'error': 'Solo il proprietario può eliminare gli export'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not document_id:
            return Response(
                {'error': 'document_id richiesto'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            sport_association = user.sport_association
        except SportAssociation.DoesNotExist:
            return Response(
                {'error': 'Associazione non trovata'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Find the archive entry
        try:
            archive = SportAssociationDocumentsArchive.objects.get(
                sport_association=sport_association,
                document__document_id=document_id,
                document__filename__startswith='export_'
            )
        except SportAssociationDocumentsArchive.DoesNotExist:
            return Response(
                {'error': 'Export non trovato'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Delete the document and archive entry
        document = archive.document
        if document.filepath:
            default_storage.delete(document.filepath)
        archive.delete()
        document.delete()

        logger.info(
            f"Export deleted",
            extra={
                'document_id': document_id,
                'sport_association_id': str(sport_association.sport_association_id),
            }
        )

        return Response({
            'success': True,
            'message': 'Export eliminato',
        })


class AssociationImportViewSet(ViewSet):
    """
    API endpoints for association data import.

    Allows importing association data from a ZIP export file
    into a fresh Bakney instance.
    """

    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [SetupTokenOrAuthenticated]

    @action(detail=False, methods=['POST'], url_path='validate')
    def validate_import(self, request):
        """
        Validate an uploaded ZIP file before importing.

        POST /api/association/import/validate/
        Content-Type: multipart/form-data

        Request body:
        - file: ZIP file to validate
        - recovery owner identity is read from the archive

        Returns:
        {
            "is_valid": true/false,
            "errors": [...],
            "warnings": [...],
            "info": {
                "export_format": "...",
                "export_date": "...",
                "association": {...}
            }
        }
        """
        uploaded_file = request.FILES.get('file')

        if not uploaded_file:
            return Response(
                {'error': 'File ZIP richiesto'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Save file to temp location
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, uploaded_file.name)

        try:
            with open(temp_path, 'wb') as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)

            # Run validation
            validator = ImportValidator(temp_path)
            validation = validator.validate_all()

            response_data = {
                'is_valid': validation.is_valid,
                'errors': validation.errors,
                'warnings': validation.warnings,
                'info': validation.info,
            }

            return Response(response_data)

        except Exception as e:
            logger.error(f"Validation error: {e}", exc_info=True)
            return Response(
                {'error': f'Errore durante la validazione: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)

    @action(detail=False, methods=['POST'], url_path='start')
    def start_import(self, request):
        """
        Start import task with uploaded ZIP file.

        POST /api/association/import/start/
        Content-Type: multipart/form-data

        Request body:
        - file: ZIP file to import
        - owner_password: Recovery password when the archive has no supported owner password hash
        Stale owner_email/preserve_uuids/skip_files fields are ignored.

        Returns:
        {
            "task_id": "...",
            "status": "started",
            "message": "..."
        }
        """
        uploaded_file = request.FILES.get('file')
        owner_password = request.data.get('owner_password', '')

        if not uploaded_file:
            return Response(
                {'error': 'File ZIP richiesto'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Save file to persistent temp location for the task
        import uuid as uuid_module
        temp_filename = f"import_{uuid_module.uuid4()}.zip"
        temp_path = f"temp/imports/{temp_filename}"
        saved_path = None

        try:
            # Save to default storage (S3/local)
            saved_path = default_storage.save(temp_path, uploaded_file)

            # Start the import task
            task = import_association_data.delay(
                zip_file_path=saved_path,
                owner_password=owner_password,
            )

            logger.info(
                f"Import task started",
                extra={
                    'task_id': task.id,
                }
            )

            return Response({
                'task_id': task.id,
                'status': 'started',
                'message': 'Import avviato. Controlla lo stato con task_id.',
            }, status=status.HTTP_202_ACCEPTED)

        except Exception as e:
            if saved_path:
                try:
                    default_storage.delete(saved_path)
                except Exception:
                    pass
            logger.error(f"Error starting import: {e}", exc_info=True)
            return Response(
                {'error': f'Errore durante avvio import: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['GET'], url_path='status')
    def import_status(self, request):
        """
        Check import task status.

        GET /api/association/import/status/?task_id=<id>

        Returns:
        {
            "task_id": "...",
            "status": "PENDING|STARTED|SUCCESS|FAILURE",
            "ready": true/false,
            "result": {...}  // Only if ready and successful
            "error": "..."   // Only if failed
        }
        """
        task_id = request.query_params.get('task_id')

        if not task_id:
            return Response(
                {'error': 'task_id richiesto'},
                status=status.HTTP_400_BAD_REQUEST
            )

        result = AsyncResult(task_id)

        response_data = {
            'task_id': task_id,
            'status': result.status,
            'ready': result.ready(),
        }

        if result.ready():
            if result.successful():
                response_data['result'] = result.result
            else:
                response_data['error'] = str(result.result)

        return Response(response_data)
