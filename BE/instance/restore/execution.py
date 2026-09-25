from instance.permissions import is_instance_administrator
"""Crash-recoverable preparation and transactional replacement."""
import logging
import os
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from django.conf import settings
from django.core.cache import cache
from django.core.files import File
from django.core.files.storage import default_storage
from django.utils import timezone
from application.services.export_service import AssociationExportService
from instance.models import DataRestore, InstanceConfiguration
from .archive import Archive, RestoreError, digest
from .replacement import require_single, replace, deletion_plan
from .locking import operation_lock, Busy
from .progress import RestoreProgress

logger = logging.getLogger(__name__)


@contextmanager
def local_copy(key):
    with tempfile.NamedTemporaryFile(suffix='.zip') as target:
        with default_storage.open(key, 'rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                target.write(chunk)
        target.flush()
        yield target.name


def save_operation(op, **changes):
    changes['updated_at'] = timezone.now()
    DataRestore.objects.filter(pk=op.pk).update(**changes)
    for key, value in changes.items():
        setattr(op, key, value)


class RecoveryExport(AssociationExportService):
    # Local rollback must retain every field of rows we delete. User accounts
    # survive replacement, so their credential exclusions still apply. Ordinary
    # migration exports keep their existing sanitization contract.
    EXCLUDED_FIELDS = {'User': AssociationExportService.EXCLUDED_FIELDS['User']}

    def __init__(self, op, progress=None):
        callback = (lambda value: progress.publish('backup', percent=value['percent'], detail=value['label'])) if progress else None
        super().__init__(op.association_id, progress_callback=callback)
        self.op = op

    def create_zip(self, source_dir, zip_filename):
        self.temporary_zip = super().create_zip(source_dir, f'recovery_{self.op.id}_{uuid.uuid4()}.zip')
        return self.temporary_zip

    def export(self, include_files=True):
        try:
            return super().export(include_files=True)
        finally:
            if getattr(self, 'temporary_zip', None):
                from pathlib import Path
                Path(self.temporary_zip).unlink(missing_ok=True)

    def save_to_storage(self, zip_path, filename):
        # Validate the complete recovery archive before authorizing any deletion.
        archive = Archive(zip_path).validate()
        if archive.missing_relations:
            raise RestoreError('Il backup di sicurezza contiene relazioni mancanti. Nessun dato è stato sostituito.')
        if archive.missing_binary_media:
            raise RestoreError('Il backup di sicurezza non contiene tutti gli allegati attuali. Correggi i file mancanti prima di ripristinare.')
        with open(zip_path, 'rb') as source:
            key = default_storage.save(f'restores/{self.op.id}/recovery/{uuid.uuid4()}.zip', File(source))
        save_operation(self.op, backup_path=key, backup_sha256=digest(zip_path))
        from docmanager.models import Document
        return Document(document_id=self.op.id, filepath=key, filename=filename)


def clean_prefix(prefix):
    """Only operation-owned temporary objects; never old business files."""
    try:
        directories, files = default_storage.listdir(prefix)
    except FileNotFoundError:
        return
    for filename in files:
        default_storage.delete(prefix + '/' + filename)
    for directory in directories:
        clean_prefix(prefix + '/' + directory)


def cleanup(op):
    try:
        if op.upload_path:
            default_storage.delete(op.upload_path)
            save_operation(op, upload_path='')
        clean_prefix(f'restores/{op.id}/upload')
        if op.state != 'completed':
            clean_prefix(f'restores/{op.id}/media')
            save_operation(op, media={})
    except Exception:
        logger.exception('Restore temporary-file cleanup will be retried: %s', op.id)


def stage_media(op, archive, progress=None):
    # After interruption, remove uncommitted objects and rebuild the mapping. The
    # durable completion state is checked before reaching this function.
    clean_prefix(f'restores/{op.id}/media')
    media = {}
    with zipfile.ZipFile(archive.path) as zf:
        total_bytes = sum(zf.getinfo(entry).file_size for entry in archive.media.values())
        copied = 0
        for index, (identity, entry) in enumerate(archive.media.items(), 1):
            # ZIP members keep their exact legacy names. A literal backslash in
            # the basename must not become a directory separator in S3/storage.
            filename = entry.rsplit('/', 1)[-1].replace('\\', '_')
            with zf.open(entry) as source:
                media[identity] = default_storage.save(
                    f'restores/{op.id}/media/{uuid.uuid4()}/{filename}', File(source))
            copied += zf.getinfo(entry).file_size
            if progress:
                progress.publish('files', completed=copied, total=total_bytes, unit='bytes',
                                 detail=f'{index} / {len(archive.media)} allegati', force=index == len(archive.media))
    save_operation(op, media=media)
    return media


def execute(operation_id):
    # OS ownership is released on process death. Redelivery can safely retry a
    # rolled-back attempt; duplicate deliveries cannot overlap or repeat a commit.
    with operation_lock(exclusive=True, required=True):
        op = DataRestore.objects.get(pk=operation_id)
        if op.state not in DataRestore.ACTIVE:
            if op.state in ('completed', 'failed', 'cancelled'):
                cleanup(op)
            return
        progress = RestoreProgress(op)
        try:
            if op.attempts >= 3:
                raise RestoreError('Ripristino interrotto più volte. I dati precedenti sono conservati; verifica il worker e carica nuovamente il backup.')
            config = InstanceConfiguration.objects.select_related('primary_association__user').get()
            require_single(config)
            if config.primary_association_id != op.association_id or not is_instance_administrator(op.owner, config):
                raise RestoreError('Il titolare o l’associazione sono cambiati. Valida nuovamente il backup.')
            if op.version != settings.RUNNING_VERSION:
                raise RestoreError('La versione dell’applicazione è cambiata. Valida nuovamente il backup.')
            save_operation(op, state='running', stage='backup', attempts=op.attempts + 1)
            progress.publish('validating', force=True)
            with local_copy(op.upload_path) as path:
                if digest(path) != op.sha256:
                    raise RestoreError('Il file caricato è cambiato dopo la validazione.')
                archive = Archive(path).validate()
                if archive.missing_media and not op.allow_missing_media:
                    raise RestoreError('Conferma esplicitamente l’assenza degli allegati indicati.')
                # A fresh snapshot is required on each retry: writes may have
                # resumed between attempts. Older recovery files remain available
                # only as private storage objects, never overwritten.
                progress.publish('backup')
                RecoveryExport(op, progress).export()
                with local_copy(op.backup_path) as backup_path:
                    if digest(backup_path) != op.backup_sha256:
                        raise RestoreError('Il backup di sicurezza non supera la verifica di integrità.')
                    backup = Archive(backup_path).validate()
                    deletion_plan(backup)
                    save_operation(op, stage='files')
                    progress.publish('files')
                    media = stage_media(op, archive, progress)
                    save_operation(op, stage='restoring')
                    def completed(importer):
                        save_operation(op, state='completed', stage='completed', error='', completed_at=timezone.now())
                    progress.publish('restoring')
                    replace(archive, backup, config, media, completed, progress=progress)
            # Clear old association/query/impersonation cache; owner credentials
            # and their database tokens are intentionally retained.
            cache.clear()
            progress.publish('completed', percent=100, force=True)
        except Exception as exc:
            # Never convert a committed replacement into a failed result.
            op.refresh_from_db()
            if op.state != 'completed':
                message = str(exc) if isinstance(exc, RestoreError) else 'Ripristino non riuscito. I dati precedenti sono conservati. Verifica il backup e riprova.'
                save_operation(op, state='failed', stage='failed', error=message)
                progress.publish('failed', force=True)
            logger.exception('Data restore %s failed', op.id)
        finally:
            op.refresh_from_db()
            if op.state not in DataRestore.ACTIVE:
                cleanup(op)
