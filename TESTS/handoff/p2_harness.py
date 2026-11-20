"""Phase 2 harness: one process set per runtime, routed and referenced by the active runtime.

Builds on TESTS/handoff/topology.py (t-p0). Adds:
  - `ModeInstallation`: an `Installation` whose simulated Docker answers the
    Phase 1 runtime authority read (TESTS/handoff/p2_docker.py) and whose
    `curl` records every readiness URL the CLI checks;
  - `render()`: the installation's production Compose rendered by the real
    Docker Compose CLI for a set of profiles and an optional process
    environment (no daemon needed);
  - `resolution()`: which running services a host name resolves to on a
    Compose network (service names and network aliases), for a mode;
  - `evidence_proxy()`: the installation's own Caddyfile in the pinned Caddy
    image, with an evidence marker per running application service. Each
    marker resolves under its service name and the network aliases Compose
    gives it, and echoes the request it received (URI, method, Host,
    forwarded headers, cookies, authorization, callback signatures, body
    length) next to `X-Fixture-Backend`/`X-Fixture-Runtime`; stopped services
    do not resolve. The update-status controller is a marker on the status
    Unix socket. Needs a Docker daemon.

The Phase 2 contract this harness names (posted on the bus, topic p2):
  runtime profiles `django` and `go`; Go scheduler service `scheduler-go`
  (/app/scheduler) and durable job worker `job-worker-go` (/app/worker); stable
  internal API alias `active-api`, held only by the active API.
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

from TESTS.handoff.topology import HERE, ROOT, Installation, caddy_image, runtime_of

SHARED = frozenset({'postgres', 'redis', 'minio', 'renderer', 'web'})
TOOLS = frozenset({'minio-init', 'migrate', 'migrate-go'})
RUNTIME_PROFILES = ('django', 'go')
DJANGO_APP = frozenset({'api', 'worker', 'beat', 'realtime-relay'})
GO_APP = frozenset({'api-go', 'worker-go', 'scheduler-go', 'job-worker-go', 'realtime-relay-go'})
APP = {'django': DJANGO_APP, 'go': GO_APP}
ACTIVE_API = {'django': 'api', 'go': 'api-go'}
RELAY = {'django': 'realtime-relay', 'go': 'realtime-relay-go'}
API_ALIAS = 'active-api'
GO_SCHEDULER, GO_JOB_WORKER = 'scheduler-go', 'job-worker-go'
WHATSAPP_GATE = 'ASSOZETA_GO_WHATSAPP_OUTBOX'
EMAIL_TASKS = 'core.tasks.send_mail_async,send_email_text,send_email_template,send_email_html'
OPERATION = '0f6c2a52-6d0e-4d1b-9a1e-7a4d0c3b2e11'


class ModeInstallation(Installation):
    """An installation whose simulated database holds a runtime authority row."""

    def __init__(self, directory, revision=None):
        super().__init__(directory, revision=revision)
        # Verification only (reference or mutation runs): files under this
        # directory replace the bundle members of the current distribution.
        overlay = os.environ.get('ASSOZETA_HANDOFF_P2_SELFHOST')
        if overlay and revision is None:
            for source in sorted(Path(overlay).rglob('*')):
                if source.is_file():
                    target = self.root / source.relative_to(overlay)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        self.authority_file = Path(directory) / 'authority.json'
        self.readiness_log = Path(directory) / 'readiness.log'
        self.failing_urls = Path(directory) / 'failing-urls'
        self.failing_urls.write_text('')
        self._tool('docker', f'exec "{sys.executable}" -I "{HERE / "p2_docker.py"}" "$@"\n')
        # Readiness is not the routing evidence (evidence_proxy is); record what
        # the lifecycle asks for. A URL listed in failing-urls fails.
        self._tool('curl', f'for last; do :; done\nprintf "%s\\n" "$last" >> "{self.readiness_log}"\n'
                           f'grep -qxF -- "$last" "{self.failing_urls}" && exit 22\nexit 0\n')

    def environment(self, **extra):
        return super().environment(HANDOFF_P2_AUTHORITY=str(self.authority_file), **extra)

    def set_authority(self, runtime, generation=None):
        generation = generation or (1 if runtime == 'django' else 2)
        self.authority_file.write_text(json.dumps({'runtime': runtime, 'generation': generation,
                                                   'operation_id': OPERATION if generation > 1 else ''}))

    def authority_unavailable(self):
        self.authority_file.write_text(json.dumps({'unavailable': True}))

    def authority_without_row(self):
        self.authority_file.write_text(json.dumps({'row': None}))

    def readiness(self):
        return self.readiness_log.read_text().splitlines() if self.readiness_log.exists() else []

    def clear_readiness(self):
        self.readiness_log.write_text('')

    def fail_url(self, url):
        with self.failing_urls.open('a') as handle:
            handle.write(url + '\n')

    def crash(self, *services):
        """These services exit right after every start (p2_docker `crashing`)."""
        state = self.observed()
        state['crashing'] = sorted(services)
        self.state.write_text(json.dumps(state, indent=1, sort_keys=True))

    def application_running(self):
        return {name for name in self.running() if name not in SHARED}

    def authority_reads(self):
        return sum(1 for event in self.observed()['events']
                   if event['call'] == ['compose', 'exec', 'postgres', 'psql', 'authority'])

    def run_python(self, code, *arguments, extra_path=()):
        """Run Python with this installation's simulated docker first on PATH."""
        environment = self.environment()
        environment['PYTHONPATH'] = os.pathsep.join([str(ROOT), *map(str, extra_path)])
        return subprocess.run([sys.executable, '-c', code, *arguments], cwd=self.root, env=environment,
                              capture_output=True, text=True, timeout=300)


def render(installation, *profiles, environment=None, env_file=None):
    """{'services': ..., 'name': ...} of the installation's production Compose."""
    env_file = Path(env_file or installation.env_file)
    arguments = [installation.real_docker, 'compose', '--env-file', str(env_file), '-f',
                 str(installation.root / 'compose.yml')]
    for profile in profiles:
        arguments += ['--profile', profile]
    values = {}
    for line in env_file.read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            values[line.split('=', 1)[0]] = None
    # As bin/assozeta's compose_with_env: installation values win over exports.
    process = {key: value for key, value in os.environ.items() if key not in values and key != 'COMPOSE_PROFILES'}
    process.update(ASSOZETA_ENV_FILE=str(env_file), ASSOZETA_SELFHOST_DIR=str(installation.root),
                   **(environment or {}))
    result = subprocess.run([*arguments, 'config', '--format', 'json'], capture_output=True, text=True,
                            env=process)
    if result.returncode != 0:
        raise AssertionError(f'docker compose config {" ".join(profiles)} failed: {result.stderr.strip()[-800:]}')
    return json.loads(result.stdout)


def long_running(services):
    """Configured long-running services (no one-shot tools, no zero-replica services)."""
    return {name for name, definition in services.items()
            if 'tools' not in (definition.get('profiles') or [])
            and (definition.get('deploy') or {}).get('replicas', 1) not in (0, '0')}


def application(services):
    return {name for name in long_running(services) if name not in SHARED}


def mode_services(installation, mode, environment=None):
    """The long-running application services Compose configures for `mode` (profile `mode`)."""
    return application(render(installation, mode, environment=environment)['services'])


def aliases(definition, network):
    networks = definition.get('networks') or {}
    if isinstance(networks, list):
        return set()
    return set(((networks.get(network) or {}).get('aliases')) or [])


def networks_of(definition):
    networks = definition.get('networks') or {}
    return set(networks) if not isinstance(networks, list) else set(networks)


def resolution(services, running, host, network):
    """Running services that `host` resolves to on `network` (service name or network alias)."""
    return {name for name in running if network in networks_of(services[name])
            and (name == host or host in aliases(services[name], network))}


def env_values(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key] = value
    return values


# ---------------------------------------------------------------- real proxy

EVIDENCE_HEADERS = {
    'uri': '{http.request.uri}', 'method': '{http.request.method}', 'host': '{http.request.host}',
    'xff': '{http.request.header.X-Forwarded-For}', 'xfp': '{http.request.header.X-Forwarded-Proto}',
    'xfh': '{http.request.header.X-Forwarded-Host}', 'cookie': '{http.request.header.Cookie}',
    'authorization': '{http.request.header.Authorization}',
    'stripe-signature': '{http.request.header.Stripe-Signature}',
    'hub-signature': '{http.request.header.X-Hub-Signature-256}',
    'content-length': '{http.request.header.Content-Length}',
}
STATIC_FILES = {
    'index.html': 'fixture frontend', 'maintenance.html': 'fixture maintenance', 'offline.html': 'fixture offline',
    'sw.js': '// fixture service worker', 'manifest.json': '{"name": "fixture"}',
    'oem/assozeta/brand/logo.svg': '<svg xmlns="http://www.w3.org/2000/svg"/>',
    'static/app.js': '// fixture bundle', 'favicon.svg': '<svg xmlns="http://www.w3.org/2000/svg"/>',
}


def marker_caddyfile(service, runtime):
    lines = ['{', '    admin off', '}', ':8000 {']
    lines += [f'    header X-Fixture-Backend "{service}"', f'    header X-Fixture-Runtime "{runtime}"']
    lines += [f'    header X-Fixture-Seen-{name} "{placeholder}"' for name, placeholder in EVIDENCE_HEADERS.items()]
    lines += ['    header Set-Cookie "sessionid=fixture-session; Path=/; HttpOnly; SameSite=Lax"',
              '    header Cache-Control "private, no-cache"',
              f'    respond "{service}" 200', '}']
    return '\n'.join(lines) + '\n'


CONTROLLER_CADDYFILE = '''{
    admin off
    auto_https off
}
http:// {
    bind unix//run/assozeta-update-status/status.sock
    header X-Fixture-Backend "controller"
    header X-Fixture-Seen-uri "{http.request.uri}"
    respond "controller status" 200
}
'''


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    @property
    def backend(self):
        return self.headers.get('x-fixture-backend')

    @property
    def runtime(self):
        return self.headers.get('x-fixture-runtime')

    def seen(self):
        return {name: self.headers.get('x-fixture-seen-' + name, '') for name in EVIDENCE_HEADERS}

    def comparable(self):
        """Everything the client and the upstream observe, except which upstream it was."""
        return {'status': self.status, 'seen': self.seen() if self.backend else None,
                'body': None if self.backend else self.body,
                **{name: self.headers.get(name) for name in (
                    'cache-control', 'set-cookie', 'referrer-policy', 'x-content-type-options', 'retry-after',
                    'server', 'content-type')}}

    def __repr__(self):
        return f'<HTTP {self.status} backend={self.backend} runtime={self.runtime}>'


@contextlib.contextmanager
def evidence_proxy(installation, running, log, maintenance=False):
    """Serve the installation's Caddyfile with markers for `running` application services.

    Yields `request(path, method='GET', headers=(), data=None) -> Response`.
    `running` is a set of service names; shared services need no marker.
    """
    image = caddy_image()
    every = render(installation, '*')['services']
    web = every['web']
    caddyfile = next(Path(volume['source']) for volume in web['volumes']
                     if volume.get('target') == '/etc/caddy/Caddyfile')
    if not caddyfile.is_absolute():
        caddyfile = installation.root / caddyfile
    suffix = uuid.uuid4().hex[:10]
    network, status_volume = f'assozeta-p2-{suffix}', f'assozeta-p2-status-{suffix}'
    containers = []
    with tempfile.TemporaryDirectory(prefix='assozeta-p2-caddy-') as directory:
        work = Path(directory).resolve()
        for name, content in STATIC_FILES.items():
            (work / 'site' / name).parent.mkdir(parents=True, exist_ok=True)
            (work / 'site' / name).write_text(content)
        (work / 'django-static').mkdir()
        (work / 'django-static/admin.css').write_text('/* fixture django static */')
        operations = work / 'operations'
        operations.mkdir()
        if maintenance:
            (operations / 'maintenance.flag').write_text('')

        def docker(*arguments):
            return subprocess.run(['docker', *arguments], check=True, stdout=subprocess.PIPE, stderr=log,
                                  timeout=120, text=True).stdout.strip()
        try:
            docker('network', 'create', '--internal', network)
            docker('volume', 'create', status_volume)
            for service in sorted(running):
                if service in SHARED:
                    continue
                definition = every[service]
                marker = work / f'{service}.Caddyfile'
                marker.write_text(marker_caddyfile(service, runtime_of(definition)))
                names = {service} | aliases(definition, 'internal')
                name = f'p2-{service}-{suffix}'
                docker('run', '--pull=never', '-d', '--rm', '--name', name, '--network', network,
                       *[argument for alias in sorted(names) for argument in ('--network-alias', alias)],
                       '-v', f'{marker}:/etc/caddy/Caddyfile:ro', image)
                containers.append(name)
            (work / 'controller.Caddyfile').write_text(CONTROLLER_CADDYFILE)
            controller = f'p2-controller-{suffix}'
            docker('run', '--pull=never', '-d', '--rm', '--name', controller, '--network', 'none',
                   '-v', f'{status_volume}:/run/assozeta-update-status',
                   '-v', f'{work / "controller.Caddyfile"}:/etc/caddy/Caddyfile:ro', image)
            containers.append(controller)
            environment = {key: str(value) for key, value in (web.get('environment') or {}).items()
                           if value is not None}
            environment.update(SITE_ADDRESS=':80', ACME_EMAIL='localhost@example.invalid')
            proxy = f'p2-web-{suffix}'
            docker('create', '--pull=never', '--name', proxy, '--network', network,
                   *[argument for key, value in environment.items() for argument in ('-e', f'{key}={value}')],
                   '-v', f'{caddyfile}:/etc/caddy/Caddyfile:ro', '-v', f'{work / "site"}:/srv:ro',
                   '-v', f'{work / "django-static"}:/django-static:ro',
                   '-v', f'{operations}:/run/assozeta-operations:ro',
                   '-v', f'{status_volume}:/run/assozeta-update-status:ro', image)
            containers.append(proxy)
            docker('start', proxy)

            def request(path, method='GET', headers=(), data=None):
                command = ['docker', 'exec', proxy, 'wget', '-S', '-O', '-', '-T', '10']
                for header in headers:
                    command += ['--header', header]
                if data is not None:
                    command += ['--post-data', data]
                command.append(f'http://127.0.0.1:80{path}')
                deadline = time.monotonic() + 30
                while True:
                    output = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    match = re.search(r'^\s*HTTP/\S+ (\d{3})', output.stderr, re.M)
                    if match or time.monotonic() > deadline:
                        break
                    time.sleep(0.5)
                found = {}
                for line in output.stderr.splitlines():
                    item = re.match(r'^\s+([A-Za-z0-9-]+):\s?(.*)$', line)
                    if item:
                        found[item.group(1).lower()] = item.group(2).strip()
                return Response(int(match.group(1)) if match else None, found, output.stdout)

            # Wait for the proxy before measuring.
            deadline = time.monotonic() + 30
            while request('/healthz').status != 200 and time.monotonic() < deadline:
                time.sleep(0.5)
            # The maintenance marker is read per request: create or remove it here.
            request.operations = operations
            yield request
        finally:
            for name in containers:
                subprocess.run(['docker', 'rm', '-f', name], stdout=log, stderr=log, timeout=60)
            subprocess.run(['docker', 'network', 'rm', network], stdout=log, stderr=log, timeout=60)
            subprocess.run(['docker', 'volume', 'rm', '-f', status_volume], stdout=log, stderr=log, timeout=60)


def configured_installation(directory, revision=None):
    """A configured (not started) installation: `bin/assozeta configure` output only."""
    installation = ModeInstallation(directory, revision=revision)
    installation.cli('configure', '--domain', 'localhost', '--version', 'test')
    return installation


def which(tool):
    found = shutil.which(tool)
    if not found:
        raise AssertionError(f'{tool} is required for this Phase 2 test')
    return found
