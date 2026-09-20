"""Verify and apply a canonical release bundle, preserving local configuration."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from uuid import UUID

from common import atomic_write, read_env, update_env, write_json
from release_catalog import API, RELEASES_URL, REPOSITORY, IMAGE_NAMES, ReleaseError, image_version, read_json, version_tuple

MAX_BUNDLE = 128 * 1024 * 1024


def managed_path(name):
    if not isinstance(name, str) or '\\' in name or '\x00' in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or str(path) != name:
        return False
    return name in ('compose.yml', 'compose.updater.yml', '.env.example', '.env.dev.example') or path.parts[0] in ('bin', 'config', 'caddy', 'updater')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def download(url):
    with urlopen(Request(url, headers={'User-Agent': 'Assozeta-Updater'}), timeout=60) as response:
        content = response.read(MAX_BUNDLE + 1)
    if len(content) > MAX_BUNDLE:
        raise ReleaseError('Release archive exceeds the supported size.')
    return content


def unpack(content):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
        total = 0
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'selfhost':
                raise ReleaseError('Unsafe release archive path.')
            if member.isdir():
                continue
            if not member.isfile():
                raise ReleaseError('Release archives must not contain links or special files.')
            total += member.size
            if total > MAX_BUNDLE:
                raise ReleaseError('Unpacked release archive exceeds the supported size.')
            relative = str(PurePosixPath(*path.parts[1:]))
            if managed_path(relative):
                if relative in files:
                    raise ReleaseError('Duplicate release archive entry.')
                files[relative] = (archive.extractfile(member).read(), member.mode & 0o777)
    return files


def validate_manifest(manifest, tag):
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or manifest.get('version') != image_version(tag):
        raise ReleaseError('This release requires a different updater protocol.')
    if not isinstance(manifest.get('bundle_sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', manifest['bundle_sha256']):
        raise ReleaseError('Release bundle digest is missing.')
    images = manifest.get('images')
    if not isinstance(images, dict) or set(images) != set(IMAGE_NAMES):
        raise ReleaseError('Release must contain exactly the supported image references.')
    for name in IMAGE_NAMES:
        expected = rf'ghcr\.io/{re.escape(REPOSITORY.split("/")[0])}/assozeta-{name}@sha256:[a-f0-9]{{64}}'
        if not isinstance(images[name], str) or not re.fullmatch(expected, images[name]):
            raise ReleaseError('Release contains an invalid image reference.')
    if not isinstance(manifest.get('files'), dict) or not manifest['files']:
        raise ReleaseError('Release file manifest is missing.')
    for name, sha in manifest['files'].items():
        if not managed_path(name) or not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
            raise ReleaseError('Invalid managed file manifest.')
    return manifest


def resolve_tag(version):
    version = image_version(version)
    for tag in (f'v{version}', version):
        try:
            raw = read_json(f'{API}/releases/tags/{tag}')
        except ReleaseError:
            continue
        if raw.get('tag_name') == tag and not raw.get('draft') and not raw.get('prerelease'):
            return tag
    raise ReleaseError('No canonical stable release exists for the selected version.')


def baseline_for_legacy(root, env_file):
    tag = resolve_tag(read_env(env_file).get('ASSOZETA_VERSION'))
    files = unpack(download(f'{RELEASES_URL}/download/{tag}/assozeta-selfhost-{tag}.tar.gz'))
    return {name: digest(value[0]) for name, value in files.items()}


def snapshot_managed(root):
    root = Path(root)
    return {str(path.relative_to(root)): digest(path.read_bytes()) for path in root.rglob('*')
            if path.is_file() and not path.is_symlink() and managed_path(str(path.relative_to(root)))}


def prepare(root, env_file, version, backup_archive=None, operation_id=None):
    root, env_file = Path(root).resolve(), Path(env_file).resolve()
    operation_id = str(UUID(operation_id)) if operation_id else None
    current = version_tuple(read_env(env_file).get('ASSOZETA_VERSION'))
    if current is None or version_tuple(version) is None or version_tuple(version) <= current:
        raise ReleaseError('The target must be newer than the installed stable version.')
    state = root / '.updater'
    state.mkdir(mode=0o700, exist_ok=True)
    pending = state / 'pending-distribution'
    if pending.exists():
        raise ReleaseError('A previous release transaction needs recovery before another update.')
    tag = resolve_tag(version)
    manifest = validate_manifest(read_json(f'{RELEASES_URL}/download/{tag}/assozeta-update.json'), tag)
    content = download(f'{RELEASES_URL}/download/{tag}/assozeta-selfhost-{tag}.tar.gz')
    if digest(content) != manifest['bundle_sha256']:
        raise ReleaseError('Release bundle checksum verification failed.')
    files = unpack(content)
    if {name: digest(value[0]) for name, value in files.items()} != manifest['files']:
        raise ReleaseError('Release file manifest does not match the archive.')

    baseline_path = state / 'managed.json'
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else baseline_for_legacy(root, env_file)
    for name in set(files) | set(baseline):
        path = root / name
        if not managed_path(name) or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root.parent):
            raise ReleaseError('Unsafe installation file path.')
        if path.exists() and not path.is_file():
            raise ReleaseError(f'A managed file conflicts with an existing directory: {name}.')
        if path.exists() and path.is_file():
            local = digest(path.read_bytes())
            desired = digest(files[name][0]) if name in files else None
            if local != baseline.get(name) and local != desired:
                raise ReleaseError(f'Local changes conflict with the release: {name}. Keep custom settings in the environment or an override file.')
    # Verify and download all immutable application/controller images before changing files.
    for reference in manifest['images'].values():
        subprocess.run(['docker', 'pull', reference], check=True)

    # Publish the recovery transaction only after its entire snapshot is durable.
    # A failed snapshot cannot leave a half-written transaction blocking retries.
    with TemporaryDirectory(prefix='snapshot-', dir=state) as directory:
        snapshot = Path(directory)
        original = {}
        for name in set(files) | set(baseline):
            path = root / name
            if path.exists():
                original[name] = {'mode': path.stat().st_mode & 0o777}
                atomic_write(snapshot / 'files' / name, path.read_bytes(), original[name]['mode'])
            else:
                original[name] = None
        atomic_write(snapshot / 'environment', env_file.read_bytes())
        previous_metadata = {}
        for name in ('managed.json', 'target-manifest.json'):
            path = state / name
            previous_metadata[name] = path.exists()
            if path.exists():
                atomic_write(snapshot / 'metadata' / name, path.read_bytes())
        write_json(snapshot / 'transaction.json', {
            'files': original, 'environment': str(env_file), 'baseline': baseline,
            'environment_mode': env_file.stat().st_mode & 0o777,
            'previous_metadata': previous_metadata,
            'phase': 'prepared', 'source_version': image_version(read_env(env_file)['ASSOZETA_VERSION']),
            'target_version': image_version(tag),
            'operation_id': operation_id,
            'recovery_image': manifest['images']['updater'],
            'backup': {'path': str(Path(backup_archive).resolve()), 'sha256': file_digest(backup_archive)} if backup_archive else None,
        })
        snapshot.rename(pending)
        descriptor = os.open(state, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    try:
        for name, (data, mode) in files.items():
            atomic_write(root / name, data, mode)
        for name in set(baseline) - set(files):
            (root / name).unlink(missing_ok=True)
        update_env(env_file, {
            'ASSOZETA_VERSION': image_version(tag),
            **{f'ASSOZETA_{name.upper()}_REF': reference for name, reference in manifest['images'].items()},
        })
        write_json(baseline_path, manifest['files'])
        write_json(state / 'target-manifest.json', manifest)
    except Exception:
        rollback(root)
        raise


def rollback(root, keep_pending=False):
    root = Path(root)
    pending = root / '.updater' / 'pending-distribution'
    transaction = json.loads((pending / 'transaction.json').read_text())
    if transaction.get('phase') != 'prepared' and not keep_pending:
        raise ReleaseError('Migrations may have changed data. Use recover-upgrade with the pre-update backup.')
    for name, attributes in transaction['files'].items():
        if attributes is None:
            (root / name).unlink(missing_ok=True)
        else:
            atomic_write(root / name, (pending / 'files' / name).read_bytes(), attributes['mode'])
    atomic_write(transaction['environment'], (pending / 'environment').read_bytes(), transaction.get('environment_mode', 0o600))
    if 'previous_metadata' in transaction:
        for name, existed in transaction['previous_metadata'].items():
            path = pending.parent / name
            if existed:
                atomic_write(path, (pending / 'metadata' / name).read_bytes())
            else:
                path.unlink(missing_ok=True)
    else:
        write_json(root / '.updater' / 'managed.json', transaction['baseline'])
    if not keep_pending:
        shutil.rmtree(pending)


def migration_started(root):
    path = Path(root) / '.updater/pending-distribution/transaction.json'
    transaction = json.loads(path.read_text())
    transaction['phase'] = 'migration_started'
    write_json(path, transaction)


def commit(root):
    pending = Path(root) / '.updater' / 'pending-distribution'
    if pending.exists():
        archive = pending.parent / 'distribution-history'
        archive.mkdir(exist_ok=True)
        import time
        pending.rename(archive / str(time.time_ns()))


if __name__ == '__main__':
    action, root, *args = sys.argv[1:]
    if action == 'prepare':
        prepare(root, *args)
    elif action == 'rollback':
        rollback(root)
    elif action == 'commit':
        commit(root)
    elif action == 'migration_started':
        migration_started(root)
    else:
        raise SystemExit('Unsupported distribution operation')
