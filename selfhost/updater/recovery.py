"""Recover a failed upgrade with its matching distribution and verified backup.

Only the host lifecycle CLI calls these functions, under its lifecycle lock.
The private HTTP runner deliberately exposes no recovery or path-taking API.
"""
import json
from pathlib import Path
import shlex
import sys
import tarfile

from common import atomic_write, read_env, write_json
from distribution import commit, file_digest, rollback
from engine import Engine
from journal import Journal, now
from release_catalog import ReleaseError, image_version


def transaction(root):
    path = Path(root) / '.updater/pending-distribution/transaction.json'
    if not path.is_file():
        raise ReleaseError('No pending release transaction is available for recovery.')
    return path, json.loads(path.read_text())


def validate(root, archive):
    path, record = transaction(root)
    backup = record.get('backup')
    if not backup or file_digest(archive) != backup['sha256']:
        raise ReleaseError('Recovery requires the unchanged backup created for this update.')
    source = image_version(read_env(path.parent / 'environment')['ASSOZETA_VERSION'])
    with tarfile.open(archive, mode='r:gz') as data:
        manifests = [member for member in data if member.name in ('manifest.txt', './manifest.txt')]
        if len(manifests) != 1 or not manifests[0].isfile() or manifests[0].size > 65536:
            raise ReleaseError('The backup version manifest is missing or invalid.')
        metadata = dict(line.split('=', 1) for line in data.extractfile(manifests[0]).read().decode().splitlines() if '=' in line)
    if image_version(metadata.get('version')) != source or record.get('source_version') != source:
        raise ReleaseError('Backup and distribution snapshot refer to different source versions.')
    return record


def restore_files(root, archive):
    root = Path(root).resolve()
    record = validate(root, archive)
    path, _ = transaction(root)
    # Keep a working entry point even if restoring the source distribution
    # replaces bin/assozeta with a release that has no recovery command.
    cli = root / '.updater/recovery-cli' / record['backup']['sha256'] / 'assozeta'
    if not cli.exists():
        atomic_write(cli, (root / 'bin/assozeta').read_bytes(), 0o700)
    arguments = [f'ASSOZETA_INSTALL_ROOT={root}', f'ASSOZETA_ENV_FILE={record["environment"]}',
                 f'ASSOZETA_RECOVERY_UPDATER_REF={record["recovery_image"]}', str(cli), 'recover-upgrade']
    atomic_write(root / '.updater/recover-upgrade', '#!/bin/sh\nexec env ' + shlex.join(arguments) + ' "$@"\n', 0o700)
    record['phase'] = 'recovering'
    write_json(path, record)
    # Keep the transaction until the data restore and running-version checks pass.
    rollback(root, keep_pending=True)


def finish(root, env_file):
    path, record = transaction(root)
    if record.get('phase') != 'recovering':
        raise ReleaseError('Recovery has not restored the source distribution.')
    if Path(env_file).resolve() != Path(record['environment']).resolve():
        raise ReleaseError('Recovery is using a different environment file.')
    Engine(root, env_file, None).verify_running(record['source_version'], allow_configured_images=True)
    journal = Journal(Path(root) / '.updater/operations.sqlite3')
    for operation in journal.records():
        if (operation['id'] == record.get('operation_id') and
                operation['status'] in ('failed', 'recovery_required') and
                image_version(operation['target_version']) == record['target_version'] and
                image_version(operation['source_version']) == record['source_version']):
            journal.update(operation['id'], status='recovered', stage='recovered', recovered_at=now(),
                           recovery=f'Ripristino della versione {record["source_version"]} completato e verificato dall’operatore del server.')
            break
    record['recovered_at'] = now()
    write_json(path, record)
    commit(root)


def reconcile(root, env_file):
    """Called under the CLI lifecycle lock, after surviving helpers are gone."""
    root = Path(root).resolve()
    if (root / '.updater/pending-distribution').exists():
        raise ReleaseError('A pending distribution requires recover-upgrade with its matching backup.')
    journal = Journal(root / '.updater/operations.sqlite3')
    unresolved = journal.requiring_recovery()
    if not unresolved:
        raise ReleaseError('No update requires reconciliation.')
    engine = Engine(root, env_file, journal)
    configured = image_version(read_env(env_file).get('ASSOZETA_VERSION'))
    for operation in unresolved:
        source, target = image_version(operation['source_version']), image_version(operation['tag'])
        if configured == target:
            # A target is successful only with the operation-bound receipt,
            # a committed distribution, and a fresh check under this lock.
            verification = engine.require_verification(operation)
            engine.verify_running(target)
            journal.update(operation['id'], status='succeeded', stage='completed', error=None,
                           verified_at=verification['verified_at'], reconciled_at=now(),
                           recovery='Aggiornamento completato e verificato dopo il riavvio del servizio.')
        elif configured == source:
            engine.verify_running(source, allow_configured_images=True)
            journal.update(operation['id'], status='recovered', stage='recovered', reconciled_at=now(),
                           recovery=f'Ripristino verificato: la versione {source} è attiva. È possibile avviare un nuovo aggiornamento.')
        else:
            raise ReleaseError('The configured version does not match the operation requiring recovery.')


if __name__ == '__main__':
    action, *arguments = sys.argv[1:]
    if action not in ('validate', 'restore_files', 'finish', 'reconcile'):
        raise SystemExit('Unknown recovery operation')
    globals()[action](*arguments)
