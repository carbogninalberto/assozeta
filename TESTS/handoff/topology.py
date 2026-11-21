"""Install a self-host distribution into a disposable root and observe its topology.

The distribution's own CLI (`bin/assozeta`) runs unmodified. Docker is replaced
by `fake_docker.py`, which renders Compose with the real Docker Compose CLI and
simulates containers, so a test can see exactly which long-running services
and one-shot containers a lifecycle command would leave behind. A Docker daemon
is not needed for this layer; the `docker compose` CLI plugin is.

`caddy_routes()` is the second boundary: the installation's own Caddyfile in
the pinned Caddy image, on a private Docker network where only the running
application services resolve. It needs a Docker daemon.
"""
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
# The plan's baseline. `feat/go-handoff` was cut here; 21648f5e differs only in
# manual documentation, and this commit is on main, so CI clones can reach it.
BASELINE_REVISION = '2284ed46'
# Bundle members, as selfhost/updater/distribution.py managed_path() defines them.
BUNDLE = ('compose.yml', 'compose.updater.yml', '.env.example', '.env.dev.example',
          'bin', 'config', 'caddy', 'updater')
GO_BINARIES = {'/app/server', '/app/celery-consumer', '/app/worker', '/app/scheduler',
               '/app/relay-go-realtime'}
# The four resident Django services of the plan (API, Celery worker, Beat, relay).
DJANGO_RESIDENT = ('api', 'worker', 'beat', 'realtime-relay')
COLD_CLIENT_FIRST_REQUEST = '/api/instance/status'  # UI/src/store/instanceStore.js


def runtime_of(definition):
    """'go' or 'django' for application processes, 'shared' for everything else."""
    image = (definition.get('image') or '').split('@', 1)[0]
    repository = image.rsplit(':', 1)[0] if ':' in image.rsplit('/', 1)[-1] else image
    name = repository.rsplit('/', 1)[-1]
    entrypoint = definition.get('entrypoint') or []
    if name == 'assozeta-backend-go' or (entrypoint and entrypoint[0] in GO_BINARIES):
        return 'go'
    if name == 'assozeta-backend':
        return 'django'
    return 'shared'


class Installation:
    """A disposable installation root holding one distribution."""

    def __init__(self, directory, revision=None):
        self.root = Path(directory) / 'install'
        self.tools = Path(directory) / 'tools'
        self.state = Path(directory) / 'docker-state.json'
        self.root.mkdir()
        self.tools.mkdir()
        self.revision = revision
        if revision is None:
            for name in BUNDLE:
                source = ROOT / 'selfhost' / name
                if source.is_dir():
                    shutil.copytree(source, self.root / name, ignore=shutil.ignore_patterns('__pycache__'))
                elif source.exists():
                    shutil.copy2(source, self.root / name)
        else:
            archive = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', revision, '--',
                                      *[f'selfhost/{name}' for name in BUNDLE]],
                                     check=True, capture_output=True).stdout
            subprocess.run(['tar', '-x', '-C', str(self.root), '--strip-components=1'],
                           input=archive, check=True, capture_output=True)
        real = shutil.which('docker')
        if not real:
            raise AssertionError('the docker CLI with the compose plugin is required for topology tests')
        self.real_docker = real
        self._tool('docker', f'exec "{sys.executable}" -I "{HERE / "fake_docker.py"}" "$@"\n')
        # Readiness probes are not the evidence here; public routing is checked
        # against the real proxy separately.
        self._tool('curl', 'exit 0\n')
        # Portable advisory lock: the CLI holds fd 8/9; flock(2) on the inherited fd.
        self._tool('flock', f'exec "{sys.executable}" -c "import fcntl,sys; '
                            f'fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX)" "$@"\n')

    def _tool(self, name, body):
        path = self.tools / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    @property
    def env_file(self):
        return self.root / '.env'

    def environment(self, **extra):
        return {'PATH': f'{self.tools}:{os.environ["PATH"]}', 'HOME': os.environ.get('HOME', '/tmp'),
                'ASSOZETA_INSTALL_ROOT': str(self.root), 'ASSOZETA_READINESS_ATTEMPTS': '1',
                'ASSOZETA_READINESS_DELAY': '1', 'HANDOFF_DOCKER_STATE': str(self.state),
                'HANDOFF_REAL_DOCKER': self.real_docker, 'LC_ALL': 'C', **extra}

    def cli(self, *arguments, check=True, timeout=300):
        result = subprocess.run([str(self.root / 'bin/assozeta'), *arguments], cwd=self.root,
                                env=self.environment(), capture_output=True, text=True, timeout=timeout)
        if check and result.returncode != 0:
            raise AssertionError(f'bin/assozeta {" ".join(arguments)} exited {result.returncode}\n'
                                 f'stdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}')
        return result

    def install(self):
        """A fresh installation by the supported CLI (version 'test' skips self-update provisioning)."""
        return self.cli('install', '--domain', 'localhost', '--version', 'test')

    def env(self):
        values = {}
        for line in self.env_file.read_text().splitlines():
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key] = value
        return values

    def set_env(self, **values):
        lines = self.env_file.read_text().splitlines()
        seen = set()
        for index, line in enumerate(lines):
            key = line.split('=', 1)[0]
            if key in values:
                lines[index] = f'{key}={values[key]}'
                seen.add(key)
        lines += [f'{key}={value}' for key, value in values.items() if key not in seen]
        self.env_file.write_text('\n'.join(lines) + '\n')

    def observed(self):
        return json.loads(self.state.read_text()) if self.state.exists() else {
            'running': [], 'definitions': {}, 'events': []}

    def running(self):
        """{service: runtime} for every simulated running service."""
        state = self.observed()
        return {name: runtime_of(state['definitions'][name]) for name in state['running']}

    def oneoffs(self):
        return [event['oneoff'] for event in self.observed()['events'] if 'oneoff' in event]

    def rendered(self, *profiles):
        arguments = [self.real_docker, 'compose', '--env-file', str(self.env_file), '-f',
                     str(self.root / 'compose.yml')]
        for profile in profiles:
            arguments += ['--profile', profile]
        result = subprocess.run([*arguments, 'config', '--format', 'json'], capture_output=True, text=True,
                                check=True, env={**os.environ, 'ASSOZETA_ENV_FILE': str(self.env_file),
                                                 'ASSOZETA_SELFHOST_DIR': str(self.root)})
        return json.loads(result.stdout)['services']

    def describe(self):
        return json.dumps({'running': self.running(), 'events': [e['call'] for e in self.observed()['events']
                                                                if e['call'][:2] != ['compose', 'config']]},
                          indent=1)


def caddy_image():
    return re.search(r'^FROM (caddy:\S+) AS production$', (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)


@contextlib.contextmanager
def caddy_routes(installation, log):
    """Serve the installation's proxy with a marker upstream per running app service.

    Each running application service resolves under its Compose service name and
    answers port 8000 with `X-Fixture-Backend: <service>` and
    `X-Fixture-Runtime: <django|go>`. Stopped services do not resolve, so a
    route to them fails at the proxy (502), as in an installation where that
    backend is absent. Yields a function `request(path) -> (status, headers)`.
    """
    image = caddy_image()
    web = installation.rendered()['web']
    caddyfile = next(Path(volume['source']) for volume in web['volumes']
                     if volume.get('target') == '/etc/caddy/Caddyfile')
    if not caddyfile.is_absolute():
        caddyfile = installation.root / caddyfile
    suffix = uuid.uuid4().hex[:10]
    network = f'assozeta-handoff-{suffix}'
    containers = []
    with tempfile.TemporaryDirectory(prefix='assozeta-handoff-caddy-') as directory:
        work = Path(directory).resolve()
        (work / 'site').mkdir()
        (work / 'site/index.html').write_text('fixture frontend')
        (work / 'operations').mkdir()

        def docker(*arguments, check=True):
            return subprocess.run(['docker', *arguments], check=check, stdout=subprocess.PIPE,
                                  stderr=log, timeout=120, text=True).stdout.strip()
        try:
            docker('network', 'create', '--internal', network)
            for service, runtime in sorted(installation.running().items()):
                if runtime == 'shared':
                    continue
                marker = work / f'{service}.Caddyfile'
                marker.write_text(':8000 {\n'
                                  f'    header X-Fixture-Backend "{service}"\n'
                                  f'    header X-Fixture-Runtime "{runtime}"\n'
                                  f'    respond "{service}" 200\n}}\n')
                name = f'handoff-{service}-{suffix}'
                docker('run', '--pull=never', '-d', '--rm', '--name', name, '--network', network,
                       '--network-alias', service, '-v', f'{marker}:/etc/caddy/Caddyfile:ro', image)
                containers.append(name)
            environment = {key: str(value) for key, value in (web.get('environment') or {}).items()
                           if value is not None}
            environment.update(SITE_ADDRESS=':80', ACME_EMAIL='localhost@example.invalid')
            proxy = f'handoff-web-{suffix}'
            docker('create', '--pull=never', '--name', proxy, '--network', network,
                   *[argument for key, value in environment.items() for argument in ('-e', f'{key}={value}')],
                   '-v', f'{caddyfile}:/etc/caddy/Caddyfile:ro', '-v', f'{work / "site"}:/srv:ro',
                   '-v', f'{work / "operations"}:/run/assozeta-operations:ro', image)
            containers.append(proxy)
            docker('start', proxy)
            # The internal network has no published ports; requests run inside the proxy.

            def request(path):
                deadline = time.monotonic() + 30
                while True:
                    output = subprocess.run(
                        ['docker', 'exec', proxy, 'wget', '-S', '-q', '-O', '/dev/null', '-T', '10',
                         f'http://127.0.0.1:80{path}'], capture_output=True, text=True, timeout=60)
                    headers = output.stderr
                    match = re.search(r'HTTP/\S+ (\d{3})', headers)
                    if match or time.monotonic() > deadline:
                        break
                    time.sleep(0.5)
                status = int(match.group(1)) if match else None
                found = dict(re.findall(r'^\s*(X-Fixture-[A-Za-z-]+):\s*(\S+)', headers, re.M))
                return status, found
            yield request
        finally:
            for name in containers:
                subprocess.run(['docker', 'rm', '-f', name], stdout=log, stderr=log, timeout=60)
            subprocess.run(['docker', 'network', 'rm', network], stdout=log, stderr=log, timeout=60)
