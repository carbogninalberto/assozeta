"""Restore a server backup and add uploaded archives, run by the update runner.

The lifecycle CLI performs the restore itself (safety backup, staging and
rollback); this module checks the request, validates archives and mirrors the
CLI's stages in the journal. Only standard archive names of the backup
directory are accepted and links are never followed.
"""
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from uuid import UUID, uuid4
import zlib

from backup_jobs import BackupRejected, _archive_path, _busy, _entry
from backups import ARCHIVE, _settings
from common import atomic_write, read_env
from journal import now, restore_recovery
from release_catalog import version_tuple

CONFIRM_WORD = 'RIPRISTINA'
STAGES = ('checking', 'backup', 'restoring', 'restarting', 'health_check')
MANIFEST_LIMIT = 65536
TIMESTAMP = re.compile(r'\d{8}T\d{6}Z')
VERSION = re.compile(r'[0-9A-Za-z.+-]{1,64}')
START_HINT = 'Se l’app non torna disponibile, avviala dal server con ./bin/assozeta start.'
INVALID = 'Il file non è un backup valido di Assozeta.'


class ArchiveInvalid(Exception):
    pass


class UploadIncomplete(Exception):
    pass


def _unsafe(name):
    return (name in ('', '..') or name.startswith('/') or name.startswith('../')
            or name.endswith('/..') or '/../' in name)


def _normal(name):
    while name.startswith('./'):
        name = name[2:]
    return name.rstrip('/')


def check_archive(stream):
    """Read a gzip tar sequentially and return its manifest values.

    Same rules as the CLI restore, a little stricter: no unsafe paths, no
    links or special files, and a database dump, objects and a manifest.
    """
    found, manifest = set(), None
    try:
        with tarfile.open(fileobj=stream, mode='r|gz') as archive:
            for member in archive:
                if _unsafe(member.name):
                    raise ArchiveInvalid('Il backup contiene percorsi non sicuri.')
                if not (member.isfile() or member.isdir()):
                    raise ArchiveInvalid('Il backup contiene collegamenti o file speciali.')
                name = _normal(member.name)
                if name == 'postgres.dump' and member.isfile():
                    found.add('database')
                elif name == 'objects' and member.isdir() or name.startswith('objects/'):
                    found.add('objects')
                elif name == 'manifest.txt' and member.isfile():
                    if manifest is not None or member.size > MANIFEST_LIMIT:
                        raise ArchiveInvalid(INVALID)
                    manifest = archive.extractfile(member).read().decode('utf-8')
    except (tarfile.TarError, EOFError, zlib.error, UnicodeDecodeError) as exc:
        raise ArchiveInvalid(INVALID) from exc
    if found != {'database', 'objects'} or manifest is None:
        raise ArchiveInvalid(INVALID)
    values = {}
    for line in manifest.splitlines():
        key, separator, value = line.partition('=')
        if separator and key not in values:
            values[key] = value.strip()
    return values


def installed_version(env_file):
    return read_env(env_file).get('ASSOZETA_VERSION', '')


def newer_reason(version, env_file):
    """Why an archive of this version cannot be restored here, if it cannot."""
    installed = installed_version(env_file)
    archived, current = version_tuple(version), version_tuple(installed)
    if archived and current and archived > current:
        return f'Il backup è della versione {version}, più recente di quella installata ({installed}). Aggiorna prima l’app.'
    return None


def start_restore(root, env_file, journal, name, body):
    if not isinstance(body, dict) or set(body) != {'request_id', 'actor_id', 'confirm'}:
        raise ValueError('Invalid restore request fields.')
    UUID(body['request_id'])
    UUID(body['actor_id'])
    if body['confirm'] != CONFIRM_WORD:
        raise BackupRejected(f'Scrivi {CONFIRM_WORD} per confermare.', 400)
    _archive_path(root, name)
    if _busy(journal):
        raise BackupRejected('C’è già un’operazione in corso. Riprova quando è finita.')
    if (Path(root) / '.updater/pending-distribution').exists():
        raise BackupRejected('Un aggiornamento in sospeso va completato prima di un ripristino.')
    entry = _entry(root, env_file, name)
    if entry is None:
        raise BackupRejected('Backup non trovato.', 404)
    if entry['pending_upgrade']:
        raise BackupRejected('Questo backup serve all’aggiornamento in sospeso.')
    reason = newer_reason(entry['version'], env_file)
    if reason:
        raise BackupRejected(reason)
    version = installed_version(env_file)
    return journal.create({'request_id': body['request_id'], 'actor_id': body['actor_id'], 'kind': 'restore',
                           'archive': name, 'release_id': None, 'tag': version, 'source_version': version})


def _open_archive(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    return os.fdopen(descriptor, 'rb')


def run_restore(root, env_file, journal, operation):
    """Check the archive, then run the CLI restore and mirror its stages."""
    root, env_file = Path(root).resolve(), Path(env_file).resolve()
    operation_id = operation['id']
    journal.update(operation_id, status='running', stage='checking')
    try:
        path = _archive_path(root, operation.get('archive'))
        with _open_archive(path) as stream:
            manifest = check_archive(stream)
        reason = newer_reason(manifest.get('version'), env_file)
        if reason:
            raise ArchiveInvalid(reason)
    except (BackupRejected, ArchiveInvalid, OSError) as exc:
        message = str(exc) if isinstance(exc, (BackupRejected, ArchiveInvalid)) else 'Il backup non è leggibile.'
        journal.update(operation_id, status='failed', stage='failed', error=message, recovery=None)
        return
    log = root / '.updater' / 'logs' / f'{operation_id}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [str(root / 'bin/assozeta'), 'restore', str(path), '--yes']
    environment = {**os.environ, 'ASSOZETA_ENV_FILE': str(env_file), 'ASSOZETA_OPERATION_ID': operation_id}
    stage, error, rollback_failed, record = 'checking', None, False, dict(operation)
    try:
        with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as output:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  env=environment, cwd=str(root)) as process:
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    if line.startswith('@@ASSOZETA_STAGE '):
                        value = line.split(' ', 1)[1].strip()
                        # Stages only move forward; the CLI's inner steps never go back.
                        if value in STAGES and STAGES.index(value) > STAGES.index(stage):
                            stage = value
                            record = journal.update(operation_id, stage=stage)
                    elif line.startswith('@@ASSOZETA_BACKUP '):
                        value = line.split(' ', 1)[1].strip()
                        if ARCHIVE.fullmatch(value):
                            record = journal.update(operation_id, safety_backup=value)
                    elif line.startswith('@@ASSOZETA_ERROR '):
                        error = line.split(' ', 1)[1].strip()
                    elif 'restore rollback failed' in line:
                        rollback_failed = True
                code = process.wait()
    except OSError:
        code = 1
    if code == 0:
        journal.update(operation_id, status='succeeded', stage='completed', verified_at=now())
    elif rollback_failed:
        journal.update(operation_id, status='recovery_required', stage='recovery_required', failed_stage=stage,
                       error=error or 'Il ripristino non è riuscito e i dati precedenti non sono stati ripristinati.',
                       recovery=restore_recovery(record))
    else:
        untouched = stage in ('checking', 'backup')
        message = error or ('Ripristino non riuscito: i dati non sono stati modificati.' if untouched
                            else 'Ripristino non riuscito: sono stati rimessi i dati precedenti.')
        journal.update(operation_id, status='failed', stage='failed', failed_stage=stage, error=message,
                       recovery=START_HINT)


class _Tee:
    """Reads exactly `length` bytes from the request and copies them to disk."""

    def __init__(self, source, sink, length):
        self.source, self.sink, self.remaining = source, sink, length

    def read(self, size=-1):
        if self.remaining <= 0:
            return b''
        size = self.remaining if size is None or size < 0 else min(size, self.remaining)
        data = self.source.read(size)
        if not data:
            raise UploadIncomplete()
        self.remaining -= len(data)
        self.sink.write(data)
        return data

    def drain(self):
        while self.remaining > 0:
            self.read(1024 * 1024)


def _size(value):
    return f'{value / 1024 ** 3:.1f} GB'.replace('.', ',')


def _publish(partial, directory):
    """Give the upload a fresh standard name, never replacing an archive."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for suffix in [''] + [f'-{number}' for number in range(1, 100)]:
        name = f'assozeta-{stamp}{suffix}.tar.gz'
        try:
            os.link(partial, directory / name)
        except FileExistsError:
            continue
        return name, stamp
    raise OSError('No free archive name')


def receive_upload(root, env_file, journal, stream, length):
    """Store an uploaded archive in the backup directory once it is valid."""
    if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
        raise ValueError('A positive Content-Length is required.')
    if _busy(journal):
        raise BackupRejected('C’è già un’operazione in corso. Riprova quando è finita.')
    root = Path(root)
    directory = root / 'backups'
    if directory.is_symlink():
        raise OSError('The backup directory must not be a symbolic link')
    directory.mkdir(mode=0o700, exist_ok=True)
    _, margin_mb = _settings(env_file)
    free = shutil.disk_usage(directory).free
    required = length + margin_mb * 1024 * 1024
    if required > free:
        raise BackupRejected(f'Spazio su disco insufficiente: servono {_size(required)}, liberi {_size(free)}.')
    lock_path = root / '.updater' / 'backup-upload.lock'
    lock_path.parent.mkdir(mode=0o700, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupRejected('È già in corso un altro caricamento.') from exc
        partial = directory / f'.upload-{uuid4().hex}.partial'
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as sink:
                tee = _Tee(stream, sink, length)
                manifest = check_archive(tee)
                tee.drain()
                sink.flush()
                os.fsync(sink.fileno())
            name, stamp = _publish(partial, directory)
        finally:
            partial.unlink(missing_ok=True)
    created = manifest.get('created_at', '')
    version = manifest.get('version', '')
    meta = [
        'origin=uploaded',
        f'created_at={created if TIMESTAMP.fullmatch(created) else stamp}',
        f'version={version if VERSION.fullmatch(version) else ""}',
    ]
    atomic_write(directory / (name + '.meta'), ''.join(f'{line}\n' for line in meta))
    return _entry(root, env_file, name)
