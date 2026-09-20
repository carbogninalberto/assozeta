"""Idempotent provisioning, executed in a one-shot updater container by the CLI."""
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys

from common import atomic_write, compose_command, compose_environment, write_json, read_env
from distribution import snapshot_managed


def credentials():
    api = Path('/run/assozeta-updater')
    api.mkdir(mode=0o700, exist_ok=True)
    token = api / 'token'
    if not token.exists():
        atomic_write(token, secrets.token_hex(32) + '\n')
    if len(token.read_text().strip()) < 32:
        raise RuntimeError('Existing updater credentials are invalid; refusing to overwrite them')
    for path in (api, token):
        os.chown(path, 10001, 10001)
    api.chmod(0o700)
    token.chmod(0o600)
    return token


def provision(root, env_file, start=True):
    root, env_file = Path(root).resolve(), Path(env_file).resolve()
    state = root / '.updater'
    state.mkdir(mode=0o700, exist_ok=True)
    values = read_env(env_file)
    project = values.get('COMPOSE_PROJECT_NAME', 'assozeta')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
        raise RuntimeError('Invalid installation project name')
    volume = f'{project}_updater_api'
    image = values.get('ASSOZETA_UPDATER_REF') or f'ghcr.io/carbogninalberto/assozeta-updater:{values["ASSOZETA_VERSION"].removeprefix("v")}'
    subprocess.run(['docker', 'volume', 'create', '--label', f'com.docker.compose.project={project}',
                    '--label', 'com.docker.compose.volume=updater_api', volume], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['docker', 'volume', 'create', '--label', f'com.docker.compose.project={project}',
                    '--label', 'com.docker.compose.volume=updater_status', f'{project}_updater_status'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['docker', 'run', '--rm', '-v', f'{volume}:/run/assozeta-updater', image,
                    '/runner/provision.py', '--credentials'], check=True)
    baseline = state / 'managed.json'
    if not baseline.exists():
        write_json(baseline, snapshot_managed(root))
    write_json(state / 'provisioning.json', {'schema_version': 2})
    external = state / 'env-mount.json'
    if not env_file.is_relative_to(root):
        write_json(external, {'services': {'updater': {'volumes': [f'{env_file.parent}:{env_file.parent}']}}})
    else:
        external.unlink(missing_ok=True)
    if start:
        subprocess.run(compose_command(root, env_file, updater=True) + ['up', '-d', '--remove-orphans'],
                       env=compose_environment(root, env_file), check=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--credentials']:
        credentials()
    else:
        provision(sys.argv[1], sys.argv[2], start='--files-only' not in sys.argv)
