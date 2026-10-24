"""Owned Compose proof of large historical logo delivery and crash-safe spools.

Uses the shipped api-go service's filesystem/security configuration with private
current-schema PostgreSQL/Redis and a deterministic S3 protocol source. This
resource/failure proof complements actual MinIO HTTP compatibility; it does not
prove installed switching, historical migrations or unbounded host disk capacity.
"""
import argparse
import base64
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.payment_category_image import DATABASE, ImageFixture, LABEL
from TESTS.http_client import Client, poll

SPOOL = '/var/cache/assozeta-logo'
MIB = 1024 * 1024
BLOCK = b''.join(hashlib.sha256(str(i).encode()).digest() for i in range(2048))
SOURCE_FILES = ('BE_GO/Dockerfile', 'selfhost/compose.yml',
                'selfhost/.env.example', 'TESTS/adapters/go/instance_logo_spool.py',
                'TESTS/adapters/go/payment_category_image.py',
                'TESTS/adapters/go/age_worker_fixture.py', 'TESTS/adapters/django/settings.py',
                'TESTS/adapters/django/production_runtime.py', 'TESTS/http_client.py')


class Progress:
    """Persist partial evidence atomically, including the phase active at cancellation."""
    def __init__(self, report, receipt):
        self.report, self.receipt = report, receipt
        self.started = time.monotonic()
        self.phase_started = {}

    def __call__(self, phase, status='started'):
        now = time.monotonic()
        if status == 'started':
            self.phase_started[phase] = now
        event = {'phase': phase, 'status': status,
                 'at': datetime.now(timezone.utc).isoformat(),
                 'elapsed_seconds': round(now - self.started, 3)}
        if phase in self.phase_started:
            event['phase_seconds'] = round(now - self.phase_started[phase], 3)
        self.report.setdefault('progress', []).append(event)
        temporary = self.receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.report, indent=2) + '\n')
        temporary.replace(self.receipt)
        print(json.dumps({'spool_progress': event}), flush=True)


class Source:
    """Read-only deterministic S3 body, with an independently observed hold."""
    def __init__(self):
        self.size = 65 * MIB
        self.hold = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.methods = []
        self.active = 0
        source = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Expected when cancellation/crash closes the SDK socket.

            def do_HEAD(self):
                self.serve(False)

            def do_GET(self):
                self.serve(True)

            def reject_write(self):
                with source.lock:
                    source.methods.append((self.command, self.path))
                self.send_response(405)
                self.send_header('Content-Length', '0')
                self.end_headers()

            do_POST = do_PUT = do_DELETE = do_PATCH = reject_write

            def serve(self, body):
                with source.lock:
                    source.methods.append((self.command, self.path))
                    size, hold = source.size, source.hold
                    source.active += 1
                try:
                    if urlsplit(self.path).path != '/logo-proof/private/instance/logo.png':
                        self.send_response(404)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header('Content-Length', str(size))
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('ETag', '"synthetic-source"')
                    self.end_headers()
                    if not body:
                        return
                    remaining = size
                    while remaining:
                        chunk = BLOCK[:min(len(BLOCK), remaining)]
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        remaining -= len(chunk)
                        if hold:
                            source.entered.set()
                            while not source.release.wait(.05):
                                if source.stopped.is_set():
                                    return
                            hold = False
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with source.lock:
                        source.active -= 1

        self.server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def configure(self, size, hold=False):
        self.release.set()
        poll(lambda: self.active, lambda count: count == 0, timeout=15)
        self.size, self.hold = size, hold
        self.entered.clear()
        self.release.clear()

    @staticmethod
    def digest(size):
        digest = hashlib.sha256()
        while size:
            chunk = BLOCK[:min(size, len(BLOCK))]
            digest.update(chunk)
            size -= len(chunk)
        return digest.hexdigest()

    def close(self):
        self.stopped.set()
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


class Deployment(ImageFixture):
    def compose(self, *args, check=True, timeout=120):
        result = subprocess.run(['docker', 'compose', '--project-name', self.identifier,
            '--file', str(self.compose_file), *map(str, args)], capture_output=True,
            text=True, timeout=timeout)
        if check and result.returncode:
            raise AssertionError('Owned Compose operation failed: ' + str(args[0]))
        return result

    def start_api(self, source):
        from django.conf import settings
        values = {
            'DBTYPE': 'POSTGRES', 'DBHOST': 'postgres', 'DBPORT': '5432',
            'DBNAME': DATABASE, 'DBUSER': 'contracts', 'DBPASSWORD': 'synthetic-only',
            'REDIS_HOST': 'redis', 'REDIS_PORT': '6379', 'REDIS_USERNAME': 'default',
            'REDIS_PASSWORD': 'synthetic-identity-only', 'GO_LISTEN_ADDR': ':8000',
            'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False',
            'AWS_ACCESS_KEY_ID': 'synthetic-access', 'AWS_SECRET_ACCESS_KEY': 'synthetic-secret',
            'AWS_STORAGE_BUCKET_NAME': 'logo-proof', 'AWS_S3_REGION_NAME': 'us-east-1',
            'AWS_S3_ENDPOINT_URL': f'http://host.docker.internal:{source.server.server_port}',
            'AWS_LOCATION': 'private', 'SECRET_KEY': settings.SECRET_KEY,
            'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': '', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': '',
            **{name + '_B64': base64.b64encode(getattr(settings, name).encode()).decode()
               for name in ('JWT_SECRET_KEY', 'JWT_PUBLIC_KEY')},
        }
        environment = self.work / 'api.env'
        self.private_values.update(str(v) for k, v in values.items()
            if any(part in k for part in ('SECRET', 'PASSWORD', 'PRIVATE')) and len(str(v)) >= 8)
        environment.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
        environment.chmod(0o600)
        operations = self.work / '.operations'
        operations.mkdir(mode=0o777)
        operations.chmod(0o777)
        render_env = {**os.environ, 'ASSOZETA_ENV_FILE': str(environment),
            'ASSOZETA_SELFHOST_DIR': str(self.work), 'ASSOZETA_BACKEND_GO_REF': self.image_id}
        rendered = subprocess.run(['docker', 'compose', '--profile', 'tools', '--env-file', str(environment),
            '-f', str(ROOT / 'selfhost/compose.yml'), 'config', '--format', 'json'],
            env=render_env, capture_output=True, text=True, timeout=30)
        assert rendered.returncode == 0, 'Production Compose render failed'
        config = json.loads(rendered.stdout)
        api, migration = config['services']['api-go'], config['services']['migrate-go']
        assert api['environment']['GO_LOGO_SPOOL_DIR'] == SPOOL
        assert 'GO_LOGO_SPOOL_DIR' not in migration['environment']
        assert not any(v['target'] == SPOOL for v in migration['volumes'])
        spool = [v for v in api['volumes'] if v['target'] == SPOOL]
        assert len(spool) == 1 and spool[0]['type'] == 'volume' and not spool[0].get('source')
        assert api['read_only'] and api['user'] == '10001:10001'
        assert api['tmpfs'] == ['/tmp:rw,noexec,nosuid,size=64m']
        self.metadata['production_service_sha256'] = hashlib.sha256(
            json.dumps({k: api[k] for k in ('user', 'read_only', 'tmpfs', 'security_opt')}, sort_keys=True).encode()).hexdigest()
        # Dependency addresses and ports are private fixture wiring. Keep the
        # production filesystem, user, init and security settings intact.
        service = copy.deepcopy(api)
        service.update(image=self.image_id, depends_on={}, restart='no',
            ports=[{'target': 8000, 'published': '0', 'host_ip': '127.0.0.1', 'protocol': 'tcp'}],
            networks={'fixture': {}}, extra_hosts=['host.docker.internal:host-gateway'],
            labels={LABEL: self.identifier})
        service.pop('container_name', None)
        manual = [v for v in service['volumes'] if v['target'] == '/app/manuale'][0]
        manual['source'] = 'manual'
        self.compose_file = self.work / 'compose.json'
        self.compose_file.write_text(json.dumps({'services': {'api-go': service},
            'networks': {'fixture': {'external': True, 'name': self.network}},
            'volumes': {'manual': {'labels': {LABEL: self.identifier}}}}))
        self.compose_file.chmod(0o600)
        self.compose('up', '--detach', '--no-build', '--pull', 'never')
        self.container = self.compose('ps', '--quiet', 'api-go').stdout.strip()
        assert self.container
        self.containers.append(self.container)
        info = self.inspect(self.container)
        mounts = [v for v in info['Mounts'] if v['Destination'] == SPOOL]
        assert len(mounts) == 1 and mounts[0]['Type'] == 'volume' and mounts[0]['RW']
        self.spool_volume = mounts[0]['Name']
        self.owned_volumes = [v['Name'] for v in info['Mounts'] if v['Type'] == 'volume']
        assert info['Image'] == self.image_id and info['HostConfig']['ReadonlyRootfs']
        assert 'size=64m' in info['HostConfig']['Tmpfs']['/tmp']
        assert self.docker('exec', self.container, 'stat', '-c', '%u:%g:%a', SPOOL).stdout.strip() == '10001:10001:700'
        revision = self.docker('exec', self.container, 'cat', '/app/CODE_REVISION').stdout.strip()
        assert revision == self.revision
        server_digest = self.docker('exec', self.container, 'sha256sum', '/app/server').stdout.split()[0]
        assert server_digest == self.build_receipt['server_sha256']
        self.port = self.published_port(self.container, 8000)
        self.base = f'http://127.0.0.1:{self.port}'
        self.ready()
        self.metadata.update(revision=revision, spool_type='anonymous local Docker volume',
            server_sha256=server_digest, build_source=self.build_receipt,
            spool_directory=SPOOL, root_readonly=True, uid=10001, mode='0700',
            tmpfs_unchanged='64MiB', automatic_restart_disabled_for_fault_observation=True)

    def ready(self):
        def probe():
            try:
                return Client(self.base, timeout=2).request('GET', '/readyz')[0] == 200
            except OSError:
                return False
        poll(probe, bool, timeout=40)

    def descriptors(self):
        script = '''for f in /proc/[0-9]*/fd/*; do
          p=$(readlink "$f" 2>/dev/null) || continue
          case "$p" in /var/cache/assozeta-logo/assozeta-instance-logo-*)
            stat -Lc '%i %h %s %b' "$f";; esac
        done'''
        out = self.docker('exec', self.container, 'sh', '-c', script).stdout
        rows = [dict(zip(('inode', 'links', 'size', 'blocks'), map(int, line.split())))
                for line in out.splitlines() if line.strip()]
        assert all(row['links'] == 0 for row in rows), 'A payload spool still has a pathname'
        assert self.docker('exec', self.container, 'ls', '-A', SPOOL).stdout == ''
        return rows

    def rss(self):
        script = '''for p in /proc/[0-9]*; do
          [ "$(readlink "$p/exe" 2>/dev/null)" = /app/server ] || continue
          awk '/^VmRSS:/{print $2}' "$p/status"
        done'''
        values = self.docker('exec', self.container, 'sh', '-c', script).stdout.split()
        assert len(values) == 1
        return int(values[0]) * 1024

    def begin(self):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=60)
        connection.connect()
        connection.request('GET', '/instance/logo.png', headers={'Accept-Encoding': 'identity'})
        return connection, connection.getresponse()

    @staticmethod
    def finish(connection, response, size, *, total_timeout=60):
        deadline = time.monotonic() + total_timeout
        try:
            assert response.status == 200, f'Logo status {response.status}'
            assert response.getheader('Content-Type') == 'image/png', 'Unexpected fallback'
            assert response.getheader('Content-Length') == str(size)
            # HTTPConnection detaches close-delimited sockets after getresponse.
            # The response still owns that socket until its body is consumed.
            body_socket = connection.sock or response.fp.raw._sock
            digest, length = hashlib.sha256(), 0
            while length < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Logo body exceeded its total download deadline')
                body_socket.settimeout(min(60, remaining))
                # read() can keep receiving a trickle without returning; read1()
                # permits checking the total deadline after every socket read.
                chunk = response.read1(64 << 10)
                if not chunk:
                    break
                digest.update(chunk)
                length += len(chunk)
            assert length == size and digest.hexdigest() == Source.digest(size)
            return {'bytes': length, 'sha256': digest.hexdigest(), 'exact_logo_bytes': True}
        finally:
            response.close()
            connection.close()

    @staticmethod
    def finish_concurrently(held, size, *, total_timeout=60):
        sockets = [connection.sock or response.fp.raw._sock for connection, response in held]
        executor = ThreadPoolExecutor(max_workers=4)
        try:
            futures = {executor.submit(Deployment.finish, connection, response, size,
                                       total_timeout=total_timeout): index
                       for index, (connection, response) in enumerate(held)}
            results = [None] * len(held)
            # Observe any failed consumer promptly, even if an earlier submitted
            # consumer is stalled; preserve input order in the final evidence.
            for future in as_completed(futures, timeout=90):
                results[futures[future]] = future.result()
            return results
        finally:
            # An assertion/timeout in one consumer must release peers before
            # joining them. Every peer also has its own total body deadline.
            for live in sockets:
                if live is not None:
                    try:
                        live.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            executor.shutdown(wait=True, cancel_futures=True)
            # A queued future cancelled before it starts never enters finish().
            for connection, response in held:
                response.close()
                connection.close()

    def empty(self):
        poll(self.descriptors, lambda rows: not rows, timeout=15)

    def crash(self, phase):
        live = self.descriptors()
        assert live and sum(row['blocks'] for row in live) > 0
        inode_ids = [row['inode'] for row in live]
        self.docker('kill', '--signal', 'KILL', self.container)
        poll(lambda: self.inspect(self.container)['State']['Running'], lambda value: not value, timeout=15)
        # Mount exactly the already observed anonymous volume in an owned helper.
        # With zero links and no remaining process descriptors, the filesystem
        # reclaims the payload blocks; directory emptiness alone is insufficient.
        result = self.docker('run', '--rm', '--network', 'none', '--read-only',
            '--user', '10001:10001', '--mount', f'type=volume,source={self.spool_volume},target={SPOOL}',
            '--entrypoint', '/bin/sh', self.image_id, '-c', f'test -z "$(ls -A {SPOOL})"')
        assert result.returncode == 0
        self.docker('start', self.container)
        # Docker can allocate a different ephemeral host port after restart.
        self.port = self.published_port(self.container, 8000)
        self.base = f'http://127.0.0.1:{self.port}'
        self.ready()
        assert any(m.get('Name') == self.spool_volume and m['Destination'] == SPOOL
                   for m in self.inspect(self.container)['Mounts'])
        self.empty()
        return {'phase': phase, 'live_unlinked_inodes': inode_ids,
                'allocated_bytes_before_kill': sum(row['blocks'] for row in live) * 512,
                'no_live_owner_after_kill': True, 'same_volume_empty_after_kill': True,
                'same_volume_restart_ready': True,
                'reclamation_basis': 'zero-link open inodes followed by process exit; no global disk-free inference'}

    def cleanup(self):
        errors = []
        if hasattr(self, 'compose_file'):
            try:
                logs = self.compose('logs', '--no-color', 'api-go', check=False)
                if self.safe_log(self.output / 'api.log', logs.stdout + logs.stderr):
                    errors.append('Fixture secret detected and redacted in API log')
                if self.compose('down', '--volumes', '--remove-orphans', check=False).returncode:
                    errors.append('Compose teardown failed')
                for volume in getattr(self, 'owned_volumes', []):
                    if self.docker('volume', 'inspect', volume, check=False).returncode == 0:
                        errors.append('Owned volume remains')
            except Exception:
                errors.append('Compose cleanup could not be verified')
        try:
            super().cleanup()
        finally:
            if errors:
                self.cleanup_verified = False
                raise AssertionError('; '.join(errors))


def exercise(proof, source, report, progress):
    progress('historical-65mib-prepare')
    baseline_rss = proof.rss()
    source.configure(65 * MIB)
    connection, response = proof.begin()
    live = proof.descriptors()
    report['first_response'] = {'status': response.status, 'type': response.getheader('Content-Type'),
        'length': response.getheader('Content-Length'), 'spool_descriptors': live}
    assert len(live) == 1 and live[0]['size'] == 65 * MIB
    progress('historical-65mib-prepare', 'completed')
    progress('historical-65mib-drain')
    report['historical_65mib'] = proof.finish(connection, response, 65 * MIB)
    proof.empty()
    progress('historical-65mib-drain', 'completed')

    progress('response-disconnect')
    source.configure(20 * MIB)
    connection, response = proof.begin()
    assert proof.descriptors(), 'Slow response did not retain its spool'
    response.close()
    connection.close()
    proof.empty()
    report['response_disconnect_releases_spool'] = True
    progress('response-disconnect', 'completed')

    progress('storage-disconnect')
    source.configure(20 * MIB, hold=True)
    canceled = HTTPConnection('127.0.0.1', proof.port, timeout=15)
    canceled.request('GET', '/instance/logo.png')
    assert source.entered.wait(10)
    poll(proof.descriptors, lambda rows: len(rows) == 1 and rows[0]['size'] > 0, timeout=10)
    canceled.close()
    proof.empty()
    report['storage_disconnect_before_source_release'] = True
    source.release.set()
    progress('storage-disconnect', 'completed')

    progress('four-held-20mib')
    source.configure(20 * MIB)
    held = [proof.begin() for _ in range(4)]
    try:
        live = poll(proof.descriptors, lambda rows: len(rows) == 4, timeout=15)
        assert all(row['size'] == 20 * MIB for row in live)
        resident = proof.rss()
        assert resident - baseline_rss < 48 * MIB, 'Payloads accumulated in process memory'
        # A fifth request waits behind the four body-held slots. Cancel its actual
        # socket, then prove the four originals and a new request still finish.
        waiter = socket.create_connection(('127.0.0.1', proof.port), timeout=3)
        waiter.sendall(b'GET /instance/logo.png HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        waiter.settimeout(.5)
        try:
            unexpected = waiter.recv(1)
            raise AssertionError(f'Fifth request did not remain pending: {unexpected!r}')
        except TimeoutError:
            pass
        finally:
            waiter.close()
        progress('four-held-20mib', 'completed')
        progress('four-20mib-drain')
        report['concurrent_20mib'] = proof.finish_concurrently(held, 20 * MIB)
        held = []
        proof.empty()
        report['fifth_waiter_cancel_recovery'] = proof.finish(*proof.begin(), 20 * MIB)
        report['resident_growth_bytes'] = resident - baseline_rss
    finally:
        for c, r in held:
            r.close()
            c.close()
    proof.empty()
    progress('four-20mib-drain', 'completed')

    progress('prepared-body-sigkill-restart')
    source.configure(20 * MIB)
    c, r = proof.begin()
    try:
        report['prepared_body_sigkill'] = proof.crash('prepared response held by slow client')
    finally:
        r.close()
        c.close()
    report['prepared_restart_exact'] = proof.finish(*proof.begin(), 20 * MIB)
    proof.empty()
    progress('prepared-body-sigkill-restart', 'completed')

    progress('partial-download-sigkill-restart')
    source.configure(20 * MIB, hold=True)
    pending = HTTPConnection('127.0.0.1', proof.port, timeout=15)
    pending.request('GET', '/instance/logo.png')
    assert source.entered.wait(10), 'Storage body never reached the owned hold'
    poll(proof.descriptors, lambda rows: len(rows) == 1 and rows[0]['size'] > 0, timeout=10)
    try:
        report['partial_download_sigkill'] = proof.crash('partially prepared S3 download')
    finally:
        pending.close()
        source.release.set()
    source.configure(20 * MIB)
    report['partial_restart_exact'] = proof.finish(*proof.begin(), 20 * MIB)
    proof.empty()
    progress('partial-download-sigkill-restart', 'completed')
    report['source_preservation'] = {'only_head_get': all(m in ('HEAD', 'GET') for m, _ in source.methods),
        'deterministic_65mib_sha256': Source.digest(65 * MIB),
        'deterministic_20mib_sha256': Source.digest(20 * MIB)}
    assert report['source_preservation']['only_head_get']
    return report


def go_hashes():
    files = subprocess.check_output(['git', 'ls-files', '--', 'BE_GO'], cwd=ROOT, text=True).splitlines()
    return {name.removeprefix('BE_GO/'): hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in files if name.endswith('.go') or name in ('BE_GO/go.mod', 'BE_GO/go.sum')}


def clean_build_receipt(args, source_hashes):
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    assert revision == args.expected_revision, 'Clean build requires exact checkout revision'
    dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all', '--',
        'BE_GO', *SOURCE_FILES], cwd=ROOT, text=True)
    assert not dirty, 'Dirty or mixed source requires an explicit source-bound build receipt'
    metadata = json.loads(ImageFixture.docker('image', 'inspect', args.image).stdout)[0]
    assert not metadata['Config'].get('Volumes'), 'Unexpected implicit image volumes'
    identifier = metadata['Id']
    data = ImageFixture.docker('run', '--rm', '--network', 'none', '--read-only',
        '--entrypoint', '/bin/sh', identifier, '-c',
        'cat /app/CODE_REVISION; sha256sum /app/server').stdout.splitlines()
    assert data[0] == revision, 'Packaged revision differs from clean checkout'
    return {'scope': 'Image built by the calling CI job from this clean exact checkout',
        'clean_revision_build': True, 'revision': revision, 'immutable_id': identifier,
        'server_sha256': data[1].split()[0], 'go_source_sha256': go_hashes(),
        'dockerfile_sha256': source_hashes['BE_GO/Dockerfile'], 'source_stable': True}


def main():
    require_production_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--build-receipt', type=Path,
        help='Required for dirty/mixed source; clean CI checkout can bind its own image and sources')
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path, default=ROOT / 'quality-reports/go-migration/instance-logo-spool')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'cleanup_verified': False, 'scope': __doc__,
        'body_total_timeout_seconds': 60,
        'payload_scope': 'Synthetic opaque bytes served as image/png; not a decodable-image or PWA proof',
        'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCE_FILES}}
    receipt = args.output / 'results.json'
    progress = Progress(report, receipt)
    progress('build-provenance')
    try:
        build_receipt = (json.loads(args.build_receipt.read_text()) if args.build_receipt else
                         clean_build_receipt(args, report['source_sha256']))
        receipt_digest = hashlib.sha256(args.build_receipt.read_bytes()).hexdigest() if args.build_receipt else None
        assert build_receipt['source_stable'] is True
        assert build_receipt['revision'] == args.expected_revision
        assert build_receipt['dockerfile_sha256'] == report['source_sha256']['BE_GO/Dockerfile']
        assert build_receipt['go_source_sha256'] and all(
            name in build_receipt['go_source_sha256'] for name in ('go.mod', 'go.sum', 'cmd/server/main.go'))
    except Exception as error:
        report['preflight_error'] = type(error).__name__
        progress('build-provenance', 'failed')
        raise
    progress('build-provenance', 'completed')
    with tempfile.TemporaryDirectory(prefix='az-logo-spool-', dir='/tmp') as directory, \
            (args.output / 'services.log').open('w') as log:
        proof = Deployment(args.image, args.expected_revision, Path(directory), args.output, log)
        proof.build_receipt = build_receipt
        source = Source()
        try:
            progress('private-services')
            proof.start_services(args.postgres, args.redis)
            assert proof.image_id == build_receipt['immutable_id']
            progress('private-services', 'completed')
            progress('current-schema')
            initialize_current_schema(Path(directory), '127.0.0.1', proof.postgres_port, DATABASE, log)
            progress('current-schema', 'completed')
            progress('compose-api')
            proof.start_api(source)
            report['image'] = proof.metadata
            progress('compose-api', 'completed')
            from django.apps import apps
            def snapshot():
                return {m._meta.label: list(m._base_manager.order_by('pk').values())
                    for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy}
            original = snapshot()
            report['checks'] = {}
            exercise(proof, source, report['checks'], progress)
            progress('managed-state-verification')
            assert snapshot() == original, 'Logo resource probes changed managed database rows'
            report['managed_models_including_auto_created'] = len(original)
            report['managed_state_unchanged'] = True
            report['checks_passed'] = True
            progress('managed-state-verification', 'completed')
        except Exception as error:
            report['execution_error'] = type(error).__name__
            raise
        finally:
            errors = []
            def cleanup_progress(phase, status='started'):
                try:
                    progress(phase, status)
                except Exception:
                    errors.append('Cleanup progress could not be persisted')

            cleanup_progress('source-cleanup')
            try:
                source.close()
                report['source_stopped'] = True
                cleanup_progress('source-cleanup', 'completed')
            except Exception:
                errors.append('Private source shutdown failed')
                cleanup_progress('source-cleanup', 'failed')
            cleanup_progress('database-cleanup')
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
                cleanup_progress('database-cleanup', 'completed')
            except Exception:
                errors.append('Private database connection cleanup failed')
                cleanup_progress('database-cleanup', 'failed')
            cleanup_progress('container-volume-cleanup')
            try:
                proof.cleanup()
                cleanup_progress('container-volume-cleanup', 'completed')
            except Exception:
                errors.append('Private container/volume cleanup failed')
                cleanup_progress('container-volume-cleanup', 'failed')
            report.update(image=proof.metadata, cleanup_verified=proof.cleanup_verified,
                source_requests=source.methods,
                cleanup_errors=errors, source_stable=all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == digest
                    for p, digest in report['source_sha256'].items()))
            report['build_source_stable'] = (
                hashlib.sha256(args.build_receipt.read_bytes()).hexdigest() == receipt_digest
                if args.build_receipt else go_hashes() == build_receipt['go_source_sha256'])
            report['success'] = bool(report.get('checks_passed') and proof.cleanup_verified
                and report.get('source_stopped') and report['source_stable']
                and report['build_source_stable'] and not errors)
            progress('finished', 'completed' if report['success'] else 'failed')
    assert report['success'], 'Packaged logo resource proof did not complete'
    print(json.dumps({'success': True, 'cleanup_verified': True, 'receipt': str(receipt)}))


if __name__ == '__main__':
    main()
