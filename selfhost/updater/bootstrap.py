"""Execute the selected release's verified lifecycle CLI against an existing install."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from common import atomic_write
from distribution import digest, download, resolve_tag, unpack, validate_manifest
from release_catalog import RELEASES_URL, ReleaseError, image_version, read_json


def bootstrap(root, env_file, version):
    root, env_file = Path(root).resolve(), Path(env_file).resolve()
    if not (root / 'bin/assozeta').is_file() or not env_file.is_file():
        raise ReleaseError('An existing configured installation is required.')
    tag = resolve_tag(version)
    metadata = validate_manifest(read_json(f'{RELEASES_URL}/download/{tag}/assozeta-update.json'), tag)
    content = download(f'{RELEASES_URL}/download/{tag}/assozeta-selfhost-{tag}.tar.gz')
    if digest(content) != metadata['bundle_sha256']:
        raise ReleaseError('Release archive checksum mismatch.')
    files = unpack(content)
    if {name: digest(value[0]) for name, value in files.items()} != metadata['files'] or 'bin/assozeta' not in files:
        raise ReleaseError('Release lifecycle script verification failed.')
    state = root / '.updater'
    state.mkdir(mode=0o700, exist_ok=True)
    with TemporaryDirectory(prefix='bootstrap-', dir=state) as directory:
        cli = Path(directory) / 'assozeta'
        atomic_write(cli, files['bin/assozeta'][0], 0o700)
        subprocess.run([str(cli), 'upgrade', image_version(tag)], check=True, env={
            **os.environ, 'ASSOZETA_INSTALL_ROOT': str(root), 'ASSOZETA_ENV_FILE': str(env_file),
            'ASSOZETA_VERIFIED_UPDATER_REF': metadata['images']['updater'],
        })


if __name__ == '__main__':
    bootstrap(*sys.argv[1:])
