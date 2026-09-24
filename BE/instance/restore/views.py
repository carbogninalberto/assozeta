import tempfile
import json
from datetime import timedelta
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from instance.models import DataRestore, InstanceConfiguration
from instance.permissions import IsInstanceOwner
from instance.tasks import restore_instance_data
from .archive import Archive, RestoreError, digest
from .execution import cleanup, save_operation
from .replacement import require_single
from .locking import operation_lock, restore_pending, Busy
from .progress import snapshot


def public_operation(op):
    return {'id': str(op.id), 'state': op.state, 'stage': op.stage, 'preview': {k: v for k, v in op.preview.items() if k != 'missing_relation_details'},
            'error': op.error, 'created_at': op.created_at, 'updated_at': op.updated_at,
            'has_recovery_backup': bool(op.backup_path), 'attempts': op.attempts, 'progress': snapshot(op)}


def recovery_page(owner, before=None):
    backups = DataRestore.objects.filter(owner=owner).exclude(backup_path='')
    if before:
        from uuid import UUID
        try:
            cursor = UUID(before)
        except ValueError:
            raise RestoreError('Pagina dei backup non valida.')
        boundary = get_object_or_404(backups, pk=cursor)
        backups = backups.filter(Q(created_at__lt=boundary.created_at) |
                                 Q(created_at=boundary.created_at, id__lt=boundary.id))
    rows = list(backups.order_by('-created_at', '-id')[:11])
    return {'backups': [public_operation(op) for op in rows[:10]],
            'backups_next': str(rows[9].id) if len(rows) > 10 else None}


class DataRestoreView(APIView):
    permission_classes = [IsInstanceOwner]
    parser_classes = [MultiPartParser, JSONParser]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response

    def get(self, request):
        config = InstanceConfiguration.get_config()
        reason = ''
        try:
            require_single(config)
            if not settings.DATA_RESTORE_LOCK_PATH:
                raise RestoreError('Aggiorna la distribuzione self-hosted per abilitare il coordinamento del ripristino.')
        except RestoreError as exc:
            reason = str(exc)
        history = DataRestore.objects.filter(owner=request.user).order_by('-created_at')[:10]
        active = DataRestore.objects.filter(state__in=DataRestore.ACTIVE, owner=request.user).first()
        return Response({'available': not reason, 'reason': reason,
                         'max_upload_bytes': settings.DATA_RESTORE_MAX_UPLOAD_BYTES,
                         'active': public_operation(active) if active else None,
                         'history': [public_operation(op) for op in history],
                         **recovery_page(request.user)})

    def post(self, request):
        try:
            with operation_lock(required=True):
                if restore_pending():
                    raise Busy('Un ripristino è già in corso.')
                config = InstanceConfiguration.get_config()
                require_single(config)
                uploaded = request.FILES.get('file')
                if not uploaded or uploaded.size > settings.DATA_RESTORE_MAX_UPLOAD_BYTES:
                    raise RestoreError('Carica un file ZIP entro il limite indicato.')
                with tempfile.NamedTemporaryFile(suffix='.zip') as temporary:
                    for chunk in uploaded.chunks():
                        temporary.write(chunk)
                    temporary.flush()
                    archive = Archive(temporary.name).validate()
                    # Keep a bounded number of review uploads per owner.
                    for previous in DataRestore.objects.filter(owner=request.user, state='review'):
                        save_operation(previous, state='cancelled', stage='cancelled')
                        cleanup(previous)
                    op = DataRestore.objects.create(owner=request.user, association_id=config.primary_association_id,
                                                    sha256=digest(temporary.name), version=settings.RUNNING_VERSION,
                                                    preview=archive.summary())
                    try:
                        temporary.seek(0)
                        key = default_storage.save(f'restores/{op.id}/upload/archive.zip', File(temporary))
                        save_operation(op, upload_path=key)
                    except Exception:
                        save_operation(op, state='failed', stage='failed', error='Caricamento non riuscito. Riprova.')
                        cleanup(op)
                        return Response({'error': op.error}, status=503)
                    return Response(public_operation(op), status=201)
        except (RestoreError, Busy) as exc:
            return Response({'error': str(exc)}, status=409 if isinstance(exc, Busy) else 400)


class DataRestoreBackupsView(DataRestoreView):
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        try:
            return Response(recovery_page(request.user, request.query_params.get('before')))
        except RestoreError as exc:
            return Response({'error': str(exc)}, status=400)


class DataRestoreActionView(DataRestoreView):
    def get(self, request, operation_id, action):
        op = get_object_or_404(DataRestore, pk=operation_id, owner=request.user)
        if action == 'missing-relations':
            response = HttpResponse(json.dumps({
                'operation_id': str(op.id), 'source_sha256': op.sha256,
                'missing_relations': op.preview.get('missing_relation_details', []),
            }, indent=2), content_type='application/json')
            response['Content-Disposition'] = f'attachment; filename="relazioni-mancanti-{op.id}.json"'
            return response
        if action == 'status':
            return Response(public_operation(op))
        if action != 'backup' or not op.backup_path:
            return Response({'error': 'Backup di sicurezza non disponibile.'}, status=404)
        return FileResponse(default_storage.open(op.backup_path, 'rb'), as_attachment=True,
                            filename=f'backup-prima-del-ripristino-{op.id}.zip', content_type='application/zip')

    def post(self, request, operation_id, action):
        try:
            # Admission is serialized with cancellation and other confirmations;
            # the worker subsequently takes the same exclusive lock.
            with operation_lock(exclusive=True, required=True):
                with transaction.atomic():
                    op = get_object_or_404(DataRestore.objects.select_for_update(), pk=operation_id, owner=request.user)
                    config = InstanceConfiguration.get_config()
                    require_single(config)
                    if action == 'dismiss':
                        if op.state not in ('failed', 'completed', 'cancelled', 'expired'):
                            raise RestoreError('Puoi chiudere solo un ripristino terminato.')
                        save_operation(op, preview={**op.preview, 'dismissed': True})
                    elif action == 'cancel':
                        if op.state != 'review':
                            raise RestoreError('Puoi annullare solo un backup in attesa di conferma.')
                        save_operation(op, state='cancelled', stage='cancelled')
                    elif action in ('start', 'resume'):
                        if op.state == 'completed':
                            return Response(public_operation(op))
                        if op.state in DataRestore.ACTIVE:
                            # This exclusive OS lock proves no restore worker is
                            # running now. Redelivery is safe and keeps the same ID.
                            pass
                        elif op.state != 'review' or action == 'resume':
                            raise RestoreError('Carica e valida nuovamente il backup.')
                        else:
                            if request.data.get('confirmation') != 'RIPRISTINA':
                                raise RestoreError('Scrivi RIPRISTINA per confermare la sostituzione.')
                            if op.created_at < timezone.now() - timedelta(hours=24):
                                raise RestoreError('La validazione è scaduta. Carica nuovamente il backup.')
                            if op.version != settings.RUNNING_VERSION or op.association_id != config.primary_association_id:
                                raise RestoreError('L’istanza è cambiata. Carica nuovamente il backup.')
                            if op.preview.get('missing_media') and request.data.get('allow_missing_media') is not True:
                                raise RestoreError('Conferma l’assenza degli allegati indicati.')
                            if restore_pending():
                                raise Busy('Un ripristino è già in corso.')
                            save_operation(op, state='queued', stage='queued', allow_missing_media=request.data.get('allow_missing_media') is True)
                    else:
                        return Response({'error': 'Azione non disponibile.'}, status=404)
                if action == 'dismiss':
                    return Response(public_operation(op))
                if op.state == 'cancelled':
                    cleanup(op)
                else:
                    try:
                        restore_instance_data.apply_async(args=[str(op.id)], task_id=str(op.id))
                    except Exception:
                        # Keep the durable queued receipt; retry via resume after
                        # broker recovery, never claim a task definitely was lost.
                        return Response({'error': 'Invio al worker non confermato. Usa Riprendi per riprovare.',
                                         'operation': public_operation(op)}, status=503)
                return Response(public_operation(op), status=202)
        except (RestoreError, Busy) as exc:
            return Response({'error': str(exc)}, status=409 if isinstance(exc, Busy) else 400)
