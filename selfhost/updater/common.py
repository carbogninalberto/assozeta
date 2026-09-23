"""Small file/process primitives shared by the trusted updater tools."""
import json
import os
from pathlib import Path
import re
import tempfile

from release_catalog import version_tuple


def update_eligibility_reason(values):
    """Keep in sync with supports_self_update in the host lifecycle CLI."""
    for service in ('BACKEND', 'WEB', 'RENDERER'):
        key = f'ASSOZETA_{service}_IMAGE'
        if values.get(key) != f'ghcr.io/carbogninalberto/assozeta-{service.lower()}':
            return f'Aggiornamenti automatici non disponibili: {key} deve indicare il repository ufficiale.'
    if version_tuple(values.get('ASSOZETA_VERSION')) is None:
        return 'Aggiornamenti automatici non disponibili: ASSOZETA_VERSION deve indicare una release stabile.'
    return None


def read_env(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if not line or line.lstrip().startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[key] = value
    return values


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content.encode() if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2) + '\n')


def update_env(path, updates):
    lines = Path(path).read_text().splitlines()
    remaining = dict(updates)
    result = []
    for line in lines:
        key = line.split('=', 1)[0]
        if key in updates:
            if key in remaining:
                result.append(f'{key}={remaining.pop(key)}')
        else:
            result.append(line)
    result.extend(f'{key}={value}' for key, value in remaining.items())
    atomic_write(path, '\n'.join(result) + '\n')


def compose_command(root, env_file, updater=False):
    command = ['docker', 'compose', '--env-file', str(env_file), '-f', str(Path(root) / ('compose.updater.yml' if updater else 'compose.yml'))]
    if updater:
        # COMPOSE_PROJECT_NAME in --env-file takes precedence over YAML `name`.
        # Use an explicit project flag so --remove-orphans cannot touch the app.
        project = read_env(env_file).get('COMPOSE_PROJECT_NAME', 'assozeta')
        command += ['--project-name', f'{project}-updater']
    external = Path(root) / '.updater' / 'env-mount.json'
    if updater and external.exists():
        command += ['-f', str(external)]
    return command


def compose_environment(root, env_file):
    # Match the lifecycle CLI: the installation env file wins over caller exports.
    values = read_env(env_file)
    result = {key: value for key, value in os.environ.items() if key not in values}
    result.update(ASSOZETA_ENV_FILE=str(env_file), ASSOZETA_SELFHOST_DIR=str(root),
                  ASSOZETA_UPDATER_STATUS_VOLUME=f'{values.get("COMPOSE_PROJECT_NAME", "assozeta")}_updater_status',
                  ASSOZETA_UPDATER_API_VOLUME=f'{values.get("COMPOSE_PROJECT_NAME", "assozeta")}_updater_api')
    return result
