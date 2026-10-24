"""Disposable real Caddy using the shipped self-host routes and pinned image."""
import contextlib
import pathlib
import re
import subprocess
import tempfile
import uuid

from TESTS.http_client import Client, poll

ROOT = pathlib.Path(__file__).resolve().parents[2]


@contextlib.contextmanager
def proxy(django, golang, log, *, operations=None, network=None, network_alias=None):
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
        configuration = (ROOT / 'selfhost/caddy/Caddyfile').read_text()
        # Substitute both original upstreams once: an injected alias such as
        # category-api:8000 must not be rewritten as the Django upstream.
        configuration = re.sub(r'api-go:8000|api:8000',
            lambda match: golang if match[0] == 'api-go:8000' else django, configuration)
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
            docker('run', '--rm', '-d', '--name', name,
                   *(['--network', network] if network else []),
                   *(['--network-alias', network_alias] if network_alias else []),
                   '--add-host', 'host.docker.internal:host-gateway',
                   '-p', '127.0.0.1::80', '-e', 'SITE_ADDRESS=:80',
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
            yield base, image
        finally:
            subprocess.run(['docker', 'logs', name], stdout=log, stderr=log, timeout=15)
            subprocess.run(['docker', 'rm', '-f', name], stdout=log, stderr=log, timeout=30, check=True)
