"""Small file/process primitives shared by the trusted updater tools."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from release_catalog import version_tuple


def update_eligibility_reason(values):
    """Keep in sync with supports_self_update in the host lifecycle CLI."""
    for service in ('BACKEND', 'WEB', 'RENDERER'):
        key = f'ASSOZETA_{service}_IMAGE'
        if values.get(key) != f'ghcr.io/carbogninalberto/assozeta-{service.lower()}':
            return f'Aggiornamenti automatici non disponibili: {key} deve indicare il repository ufficiale.'
    # Historical installations omit Go; Compose defaults absent/blank values to the official image.
    if values.get('ASSOZETA_BACKEND_GO_IMAGE') not in (None, '', 'ghcr.io/carbogninalberto/assozeta-backend-go'):
        return 'Aggiornamenti automatici non disponibili: ASSOZETA_BACKEND_GO_IMAGE deve indicare il repository ufficiale.'
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
    if not updater and edge_enabled(env_file):
        # Same optional front-proxy overlay as bin/assozeta.
        command += ['-f', str(Path(root) / EDGE_OVERLAY)]
    if not updater and go_tasks_enabled(env_file):
        # Same opt-in Go task consumer profile as bin/assozeta.
        command += ['--profile', GO_TASKS_PROFILE]
    return command


RUNTIME_PROFILES = {'django', 'go'}
SHARED_SERVICES = {'postgres', 'redis', 'minio', 'renderer', 'web'}


def has_runtime_profiles(services):
    return any(RUNTIME_PROFILES.intersection(service.get('profiles', ())) for service in services.values())


def effective_runtime(root, env_file, services):
    # Historical distributions predate both the profiles and the authority row.
    if has_runtime_profiles(services):
        from runtime_switch import AuthorityUnavailable, compose_authority, read_authority
        try:
            return read_authority(root, env_file).runtime
        except AuthorityUnavailable:
            # The switch controller needs a full generation. Lifecycle commands
            # can use the fresh-install default only after confirming no row exists.
            authority = compose_authority(root, env_file)
            try:
                probe = subprocess.run(authority.command, env=authority.environment, text=True, timeout=20,
                                       input='SELECT singleton FROM public.assozeta_go_job_ownership WHERE singleton=1;',
                                       capture_output=True)
            except (OSError, subprocess.SubprocessError):
                raise AuthorityUnavailable() from None
            if probe.returncode == 0 and probe.stdout == '':
                return 'django'
            raise
    return None


def runtime_services(root, env_file, runtime, *, configured=None):
    """Application services from this installation, including legacy mixed deployments."""
    if configured is None:
        command = compose_command(root, env_file) + ['--profile', '*', 'config', '--format', 'json']
        configured = json.loads(subprocess.check_output(command, env=compose_environment(root, env_file),
                                                        text=True, timeout=30))['services']
    if has_runtime_profiles(configured):
        if runtime not in RUNTIME_PROFILES:
            raise ValueError('Unknown application runtime')
        return sorted(name for name, service in configured.items() if runtime in service.get('profiles', ()))

    # Legacy render with no runtime profile: unprofiled apps plus the selected
    # consumer, excluding tools and the relay scaled to zero.
    legacy_profiles = {GO_TASKS_PROFILE} if go_tasks_enabled(env_file) else set()
    return sorted(name for name, service in configured.items()
                  if name not in SHARED_SERVICES and service.get('deploy', {}).get('replicas', 1) != 0
                  and (not service.get('profiles') or legacy_profiles.intersection(service['profiles'])))


GO_TASKS_PROFILE = 'go-tasks'


def go_tasks_enabled(env_file):
    return read_env(env_file).get('ASSOZETA_GO_TASKS') == '1'


EDGE_OVERLAY = 'config/compose.edge.yml'


def edge_enabled(env_file):
    return bool(read_env(env_file).get('ASSOZETA_EDGE_NETWORK'))


def compose_environment(root, env_file):
    # Match the lifecycle CLI: the installation env file wins over caller exports.
    values = read_env(env_file)
    result = {key: value for key, value in os.environ.items() if key not in values}
    result.update(ASSOZETA_ENV_FILE=str(env_file), ASSOZETA_SELFHOST_DIR=str(root),
                  ASSOZETA_UPDATER_STATUS_VOLUME=f'{values.get("COMPOSE_PROJECT_NAME", "assozeta")}_updater_status',
                  ASSOZETA_UPDATER_API_VOLUME=f'{values.get("COMPOSE_PROJECT_NAME", "assozeta")}_updater_api')
    return result
