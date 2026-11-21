"""Disposable real Caddy using the shipped self-host routes and pinned image."""
import contextlib
import json
import pathlib
import re
import subprocess
import tempfile
import uuid

from TESTS.http_client import Client, poll

ROOT = pathlib.Path(__file__).resolve().parents[2]


def fixture_routes(configuration, django, golang):
    """The shipped routes with each API upstream pointed at a fixture backend.

    Historical dual-API Caddyfile (api:8000 and api-go:8000 by path): both
    original upstreams are substituted once, so an injected alias such as
    category-api:8000 is never rewritten as the Django upstream. This includes
    the default of an env placeholder such as
    {$ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM:api:8000}, so the default case keeps
    reaching the Django fixture; `environment` can override it.

    Exclusive Caddyfile (every API route reaches the single active runtime
    through active-api:8000, Phase 2 of the Django/Go switch): to keep comparing
    two implementations behind the same rewrites, the upstream inside a
    `handle`/`handle_path` block whose path starts with /api/v2 becomes the Go
    fixture and every other one the Django fixture. That split is a harness
    choice, not production routing; production routing is pinned by
    TESTS/handoff/test_p2_routing.py.
    """
    if 'active-api:8000' not in configuration:
        return re.sub(r'api-go:8000|api:8000',
                      lambda match: golang if match[0] == 'api-go:8000' else django, configuration)
    lines, blocks, depth = [], [], 0
    for line in configuration.splitlines(keepends=True):
        opened = re.match(r'\s*handle(?:_path)?\s+(\S+)\s*\{', line)
        if opened:
            blocks.append((opened.group(1), depth))
        if 'active-api:8000' in line:
            matcher = blocks[-1][0] if blocks else ''
            line = line.replace('active-api:8000', golang if matcher.startswith('/api/v2') else django)
        depth += line.count('{') - line.count('}')
        while blocks and depth <= blocks[-1][1]:
            blocks.pop()
        lines.append(line)
    return ''.join(lines)


@contextlib.contextmanager
def proxy(django, golang, log, *, operations=None, network=None, network_alias=None, container_observer=None, pull_policy=None,
          environment=None, caddyfile=None):
    """`caddyfile` defaults to this checkout's selfhost/caddy/Caddyfile."""
    if pull_policy not in (None, 'never'):
        raise ValueError('Unsupported proxy pull policy')
    if network_alias and not network:
        raise ValueError('A proxy network alias requires an owned Docker network')
    image = re.search(r'^FROM (caddy:\S+) AS production$',
                      (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)
    name = 'assozeta-routing-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='assozeta-routing-') as directory:
        work = pathlib.Path(directory).resolve()
        state = pathlib.Path(operations) if operations else work / 'operations'
        state.mkdir(exist_ok=True)
        (work / 'site').mkdir()
        (work / 'site/index.html').write_text('fixture frontend')
        (work / 'site/maintenance.html').write_text('fixture maintenance')
        configuration = fixture_routes(
            pathlib.Path(caddyfile or ROOT / 'selfhost/caddy/Caddyfile').read_text(), django, golang)
        # Observe the container's client address independently of application
        # audit rows. Only this disposable proxy's own health response is extended;
        # upstream headers, admission and application routes remain unchanged.
        configuration = configuration.replace('handle /healthz {',
            'handle /healthz {\n            header X-Fixture-Client-IP {remote_host}', 1)
        (work / 'Caddyfile').write_text(configuration)
        def docker(*args):
            return subprocess.run(['docker', *args], check=True, stdout=subprocess.PIPE,
                                  stderr=log, timeout=120).stdout.decode().strip()
        try:
            docker('run', *(['--pull=never'] if pull_policy == 'never' else []),
                   '--rm', '-d', '--name', name,
                   *(['--network', network] if network else []),
                   *(['--network-alias', network_alias] if network_alias else []),
                   '--add-host', 'host.docker.internal:host-gateway',
                   '-p', '127.0.0.1::80', '-e', 'SITE_ADDRESS=:80',
                   *[argument for name, value in (environment or {}).items()
                     for argument in ('-e', f'{name}={value}')],
                   '-v', f'{work}/Caddyfile:/etc/caddy/Caddyfile:ro',
                   '-v', f'{work}/site:/srv:ro',
                   '-v', f'{state}:/run/assozeta-operations:ro', image)
            port = int(docker('port', name, '80/tcp').rsplit(':', 1)[1])
            base = f'http://127.0.0.1:{port}'
            def ready():
                try:
                    return Client(base, timeout=1).request('GET', '/healthz')[0] == 200
                except OSError:
                    return False
            poll(ready, bool, timeout=30)
            docker('exec', name, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile')
            if container_observer is not None:
                container_observer(json.loads(docker('inspect', '--format',
                    '{"container_id":{{json .Id}},"image_id":{{json .Image}}}', name)))
            yield base, image
        finally:
            # Log collection is diagnostic. Owned-container removal must still
            # run when it times out or raises for any other reason.
            try:
                subprocess.run(['docker', 'logs', name], stdout=log, stderr=log, timeout=15)
            finally:
                subprocess.run(['docker', 'rm', '-f', name], stdout=log, stderr=log,
                               timeout=30, check=True)
