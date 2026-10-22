"""Disposable PostgreSQL/PgBouncer fixture with transaction pooling.

Runtime clients use pooled_dsn. direct_dsn is only for fixture administration,
controlling SQL barriers, and independent durable-state observations.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

IMAGE = 'assozeta-go-pooler-fixture:pg16.15-pgbouncer1.25.2'
DOCKERFILE = (
    'FROM postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea\n'
    'RUN apk add --no-cache pgbouncer=1.25.2-r0\n')
RECIPE_LABEL = 'org.assozeta.fixture.recipe-sha256'
RECIPE_SHA256 = hashlib.sha256(DOCKERFILE.encode()).hexdigest()


def _docker(*arguments, check=True, timeout=60):
    return subprocess.run(['docker', *map(str, arguments)], text=True,
                          capture_output=True, check=check, timeout=timeout)


def _ensure_image(output):
    existing = _docker('image', 'inspect', IMAGE, check=False)
    if existing.returncode == 0:
        labels = json.loads(existing.stdout)[0]['Config'].get('Labels') or {}
        if labels.get(RECIPE_LABEL) == RECIPE_SHA256:
            return
    with tempfile.TemporaryDirectory(prefix='az-pooler-build-') as directory:
        (Path(directory) / 'Dockerfile').write_text(DOCKERFILE)
        build = _docker('build', '--label', RECIPE_LABEL + '=' + RECIPE_SHA256,
                        '-t', IMAGE, directory, check=False, timeout=180)
        (output / 'image-build.log').write_text(build.stdout + build.stderr)
        build.check_returncode()


@dataclass(frozen=True)
class PoolerFixture:
    direct_dsn: str
    pooled_dsn: str
    metadata: dict
    container_name: str


@contextmanager
def private_pooler(output, database):
    """Yield isolated real services; callers record success after this exits.

    Cleanup removes the fixture container and its anonymous volume. A failure to
    verify cleanup propagates rather than producing a successful adapter receipt.
    The uniquely tagged fixture image is retained for reuse by other adapters.
    """
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', database):
        raise ValueError('A simple private fixture database name is required')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    _ensure_image(output)
    name = 'az-pooler-' + uuid.uuid4().hex[:12]
    metadata = {'private_container_removed': False, 'private_volumes_removed': False}
    volumes = []
    attempted = False
    with tempfile.TemporaryDirectory(prefix='az-pooler-config-') as directory:
        config = Path(directory)
        config.chmod(0o755)
        (config / 'userlist.txt').write_text('"contracts" ""\n')
        (config / 'pgbouncer.ini').write_text(
            f'[databases]\n{database} = host=127.0.0.1 port=5432 dbname={database}\n'
            '[pgbouncer]\nlisten_addr = 0.0.0.0\nlisten_port = 6432\n'
            'auth_type = trust\nauth_file = /proof/userlist.txt\n'
            'admin_users = contracts\nstats_users = contracts\n'
            'pool_mode = transaction\ndefault_pool_size = 2\n'
            'max_client_conn = 30\nmax_prepared_statements = 0\n'
            'ignore_startup_parameters = extra_float_digits\n'
            'logfile = /tmp/pgbouncer.log\npidfile = /tmp/pgbouncer.pid\n')
        for path in config.iterdir():
            path.chmod(0o644)
        try:
            attempted = True
            _docker('run', '-d', '--name', name,
                    '--label', 'org.assozeta.fixture=transaction-pooler',
                    '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_USER=contracts',
                    '-e', 'POSTGRES_DB=' + database, '-p', '127.0.0.1::5432',
                    '-p', '127.0.0.1::6432', '-v', f'{config}:/proof:ro', IMAGE)
            state = json.loads(_docker('inspect', name).stdout)[0]
            volumes = [mount['Name'] for mount in state['Mounts'] if mount['Type'] == 'volume']
            for _ in range(100):
                if _docker('exec', name, 'pg_isready', '-U', 'contracts', '-d', database,
                           check=False).returncode == 0:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Private PostgreSQL did not become ready')
            _docker('exec', '-d', '--user', 'postgres', name, 'pgbouncer', '/proof/pgbouncer.ini')
            for _ in range(100):
                settings = _docker('exec', name, 'psql', '-h', '127.0.0.1', '-p', '6432',
                                   '-U', 'contracts', '-d', 'pgbouncer', '-Atc', 'SHOW CONFIG',
                                   check=False)
                if settings.returncode == 0:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Private PgBouncer did not become ready')
            (output / 'pgbouncer-config.log').write_text(settings.stdout)
            if 'pool_mode|transaction|' not in settings.stdout or 'max_prepared_statements|0|' not in settings.stdout:
                raise AssertionError('Fixture must use transaction pooling without named prepared statements')
            pooled = _docker('port', name, '6432/tcp').stdout.strip().split(':')[-1]
            direct = _docker('port', name, '5432/tcp').stdout.strip().split(':')[-1]
            if not pooled.isdigit() or not direct.isdigit() or pooled == direct:
                raise AssertionError('Distinct private loopback ports are required')
            metadata.update(
                image_id=_docker('image', 'inspect', IMAGE, '--format', '{{.Id}}').stdout.strip(),
                pgbouncer_version=_docker('exec', name, 'pgbouncer', '--version').stdout.strip(),
                postgres_version=_docker('exec', name, 'postgres', '--version').stdout.strip(),
                pool_mode='transaction', max_prepared_statements=0,
                direct_port=int(direct), pooled_port=int(pooled), recipe_sha256=RECIPE_SHA256)
            suffix = f'user=contracts dbname={database} sslmode=disable'
            yield PoolerFixture(f'host=127.0.0.1 port={direct} {suffix}',
                                f'host=127.0.0.1 port={pooled} {suffix}', metadata, name)
        finally:
            if attempted:
                try:
                    logs = _docker('logs', name, check=False)
                    (output / 'postgres.log').write_text(logs.stdout + logs.stderr)
                    (output / 'pgbouncer.log').write_text(
                        _docker('exec', name, 'cat', '/tmp/pgbouncer.log', check=False).stdout)
                finally:
                    try:
                        _docker('rm', '-f', '-v', name, check=False)
                    finally:
                        metadata['private_container_removed'] = not _docker(
                            'container', 'ls', '-aq', '--filter', 'name=^/' + name + '$').stdout.strip()
                        metadata['private_volumes_removed'] = all(not _docker(
                            'volume', 'ls', '-q', '--filter', 'name=' + volume).stdout.strip() for volume in volumes)
                        if not metadata['private_container_removed'] or not metadata['private_volumes_removed']:
                            raise RuntimeError('Private pooler fixture cleanup could not be confirmed')
                        (output / 'fixture.json').write_text(json.dumps(metadata, indent=2) + '\n')
