"""Run the existing real 25-second category shutdown oracle on an immutable image.

This proves current-schema packaged process/HTTP/PostgreSQL shutdown only.
It does not prove Caddy or Django shutdown, installation, switching, or broader
route parity. Docker and real PostgreSQL acceptance are required separately from
the protocol unit controls shipped beside this adapter.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from TESTS.adapters.go.payment_category_image import DATABASE, LABEL, ImageFixture
from TESTS.adapters.go.read_route_locales_image import (
    go_source_hashes, verify_build_receipt, verify_binary_digest,
)

AUDIT_CLOCK_BOUNDARIES = (
    'graceful.request_started', 'graceful.blocker_release_before',
    'graceful.delete_completed', 'graceful.recovery_started', 'graceful.recovery_completed',
    'expired.request_started', 'expired.blocker_released',
    'expired.recovery_started', 'expired.recovery_completed',
)
AUDIT_CLOCK_QUERY = 'SELECT clock_timestamp()'

ORACLE_SOURCES = (
    'TESTS/adapters/go/payment_category_shutdown_image.py',
    'TESTS/adapters/go/test_payment_category_shutdown_image.py',
    'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/payment_category_server.py',
    'TESTS/adapters/go/read_route_locales_image.py',
    'TESTS/adapters/go/impersonation_management_image.py',
    'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/go/server.py',
    'TESTS/adapters/django/payment_category_shutdown.py',
    'TESTS/test_payment_category_shutdown_oracle.py',
    'TESTS/adapters/django/production_runtime.py',
    'TESTS/adapters/django/settings.py',
    'TESTS/adapters/django/accounts.py',
    'TESTS/http_client.py',
    'selfhost/.env.example',
    'BE/Dockerfile', 'BE/requirements.txt', 'BE/requirements-test.txt',
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes(source):
    paths = set((source / 'BE').rglob('*.py'))
    paths.update(source / name for name in ORACLE_SOURCES)
    return {path.relative_to(source).as_posix(): sha256(path) for path in sorted(paths)}


def listener_state(tables, port=8000):
    """Observe the application namespace, not Docker's host forwarding socket."""
    assert set(tables) == {'tcp', 'tcp6'}, 'Both socket tables are required'
    listeners = []
    for name, text in tables.items():
        lines = text.splitlines()
        assert lines and 'local_address' in lines[0], 'Unreadable socket table'
        for line in lines[1:]:
            fields = line.split()
            assert len(fields) >= 4, 'Malformed socket table row'
            address, raw_port = fields[1].rsplit(':', 1)
            if int(raw_port, 16) == port and fields[3] == '0A':
                listeners.append({'family': name, 'address': address, 'port': port})
    return {'closed': not listeners, 'method': 'owned PID1 network namespace /proc/net/tcp{,6}',
            'listeners': listeners}


class ContainerProcess:
    """Popen-shaped ownership handle backed by the real container PID1 state."""
    def __init__(self, fixture, container, executable, log, identity):
        self.fixture, self.container, self.log = fixture, container, log
        self.args = [str(executable)]
        self.pid = identity['host_pid']
        self.identity = identity
        self.returncode = None
        self.logged = ''

    def sync_log(self):
        result = self.fixture.docker('logs', self.container)
        contents = result.stdout + result.stderr
        assert contents.startswith(self.logged), 'Container diagnostic log changed'
        self.log.write(contents[len(self.logged):])
        self.log.flush()
        self.logged = contents

    def poll(self):
        info = self.fixture.inspect(self.container)
        assert info['Id'] == self.identity['container_id'], 'Owned container identity changed'
        assert info['Image'] == self.identity['immutable_id'], 'Owned image changed'
        assert info['Config']['Labels'].get(LABEL) == self.fixture.identifier
        state = info['State']
        assert not state.get('OOMKilled'), 'Packaged server was OOM-killed'
        assert not state.get('Error'), 'Packaged server runtime failed'
        if state['Running']:
            return None
        assert state['Status'] in ('exited', 'dead'), 'Unrecognized terminal state'
        self.returncode = state['ExitCode']
        self.identity['exit_status'] = self.returncode
        self.identity['finished_at'] = state['FinishedAt']
        self.sync_log()
        return self.returncode

    def send_signal(self, number):
        assert number in (signal.SIGTERM, signal.SIGKILL), 'Unexpected shutdown signal'
        assert self.poll() is None, 'Cannot signal an already stopped server'
        name = 'TERM' if number == signal.SIGTERM else 'KILL'
        self.fixture.docker('kill', '--signal', name, self.container)
        self.identity.setdefault('signals', []).append({
            'signal': 'SIG' + name, 'sent_at': datetime.now(timezone.utc).isoformat()})

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def kill(self):
        self.send_signal(signal.SIGKILL)

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(['owned-packaged-server'], timeout)
            time.sleep(.05)
        return self.returncode


class ShutdownImageFixture(ImageFixture):
    def __init__(self, image, revision, work, output, log, binary_sha256):
        super().__init__(image, revision, work, output, log)
        self.expected_binary = binary_sha256
        self.processes = []
        self.active = None
        self.active_port = None
        self.operations = work / 'shared-operations'
        self.audit_kernel_id = None

    def run_container(self, suffix, image, *args):
        if suffix == 'server':
            # Keep one lifecycle volume across all four processes, like native
            # restart recovery. Per-launch environment/executable files stay separate.
            self.operations.mkdir(mode=0o777, exist_ok=True)
            self.operations.chmod(0o777)
            args = list(args)
            mounts = [index + 1 for index, arg in enumerate(args)
                      if arg == '--mount' and args[index + 1].endswith(',target=/operations')]
            assert len(mounts) == 1, 'Expected exactly one lifecycle volume'
            args[mounts[0]] = f'type=bind,source={self.operations},target=/operations'
        return super().run_container(suffix, image, *args)

    def audit_clock_source(self):
        # PostgreSQL and Go use CLOCK_REALTIME in the same Docker Linux kernel.
        # Host wall time can differ on Docker Desktop, so it is not an audit bound.
        kernel_id = self.docker('exec', self.postgres, 'cat',
                                '/proc/sys/kernel/random/boot_id').stdout.strip()
        assert re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', kernel_id)
        self.audit_kernel_id = kernel_id
        return {'kind': 'postgresql_clock_timestamp', 'query': AUDIT_CLOCK_QUERY,
                'database': DATABASE, 'clock_domain': 'shared Docker Linux kernel CLOCK_REALTIME',
                'kernel_boot_id': kernel_id}

    def read_audit_time(self):
        from django.db import connection

        with connection.cursor() as cursor:
            # clock_timestamp(), unlike CURRENT_TIMESTAMP, is evaluated now and
            # cannot reuse the start time of an earlier database transaction.
            cursor.execute(AUDIT_CLOCK_QUERY)
            return cursor.fetchone()[0]

    def database_guard(self, work, database):
        assert work == self.work and work.parent == Path('/tmp')
        assert work.name.startswith('az-category-shutdown-image-')
        assert database['NAME'] == DATABASE and database['USER'] == 'contracts'
        assert database['HOST'] == '127.0.0.1'
        assert int(database['PORT']) == self.postgres_port
        info = self.inspect(self.postgres)
        assert info['State']['Running']
        assert info['Config']['Labels'].get(LABEL) == self.identifier
        assert info['Image'] == self.metadata['postgres_image']

    def listener_probe(self, port):
        assert self.active is not None and port == self.active_port
        if self.active.poll() is not None:
            return {'closed': True, 'method': 'owned container PID1 exited',
                    'exit_status': self.active.returncode}
        tables = {name: self.docker('exec', self.active.container, 'cat', '/proc/net/' + name).stdout
                  for name in ('tcp', 'tcp6')}
        return listener_state(tables)

    @contextmanager
    def server(self, work, log, tool_env, address, *, process_observer=None, versioned=False):
        assert process_observer is not None and not versioned
        ordinal = len(self.processes) + 1
        server_work = self.work / f'server-{ordinal}'
        server_work.mkdir(mode=0o700)
        container = self.identifier + '-server'
        process = None
        try:
            with super().server(server_work, log, tool_env, address) as runtime:
                info = self.inspect(container)
                assert info['Image'] == self.image_id == self.requested_image
                assert info['Config']['Entrypoint'] == ['/app/server']
                assert info['Config']['User'] == '10001:10001'
                kernel_id = self.docker('exec', container, 'cat',
                                        '/proc/sys/kernel/random/boot_id').stdout.strip()
                assert self.audit_kernel_id is not None and kernel_id == self.audit_kernel_id, (
                    'Packaged server and audit clock must share the same Linux kernel',
                )
                assert info['HostConfig']['ReadonlyRootfs'] is True
                assert not any(m['Destination'] == '/app' or m['Destination'].startswith('/app/')
                               for m in info.get('Mounts', [])), 'Executable/package mount forbidden'
                status = self.docker('exec', container, 'cat', '/proc/1/status').stdout
                uid = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
                assert uid == ['10001'] * 4, 'PID1 must run as UID10001'
                assert self.docker('exec', container, 'readlink', '/proc/1/exe').stdout.strip() == '/app/server'
                running_digest = self.docker('exec', container, 'sha256sum', '/proc/1/exe').stdout.split()[0]
                verify_binary_digest(running_digest, self.expected_binary)
                verify_binary_digest(runtime['metadata']['server_sha256'], self.expected_binary)
                executable = server_work / 'packaged-server'
                self.docker('cp', container + ':/app/server', executable)
                executable.chmod(0o500)
                verify_binary_digest(sha256(executable), self.expected_binary)
                identity = {'container': container, 'container_id': info['Id'],
                            'immutable_id': info['Image'],
                            'host_pid': info['State']['Pid'], 'container_pid': 1,
                            'revision': self.revision, 'uid': 10001, 'readonly_root': True,
                            'kernel_boot_id': kernel_id,
                            'mounted_executables': False, 'server_sha256': running_digest,
                            'package_sha256': runtime['metadata']['server_sha256'],
                            'extracted_sha256': sha256(executable)}
                assert type(identity['host_pid']) is int and identity['host_pid'] > 0
                self.processes.append(identity)
                process = ContainerProcess(self, container, executable, log, identity)
                self.active = process
                self.active_port = self.published_port(container, 8000)
                process.sync_log()
                process_observer(process)
                yield runtime
        finally:
            # The shared image fixture stops only after the behavioral oracle has
            # completed or failed. It never supplies the successful drain outcome.
            if process is not None:
                process.poll()
                stopped_name = self.identifier + f'-server-{ordinal}'
                self.docker('rename', container, stopped_name)
                self.containers[self.containers.index(container)] = stopped_name
                process.container = stopped_name
                process.identity['container'] = stopped_name


def assert_oracle(result, image_id, binary_sha256, processes):
    from TESTS.adapters.django.payment_category_shutdown import (
        DRAIN_SECONDS, EXIT_CLEANUP_TOLERANCE_SECONDS, EXPECTED_CASES,
    )
    assert DRAIN_SECONDS == 25 and EXIT_CLEANUP_TOLERANCE_SECONDS == 6.5
    for key in ('success', 'cleanup_verified', 'source_stable', 'fixture_snapshot_restored',
                'packaged_binary_all_four_matches'):
        assert result.get(key) is True, key
    assert result.get('runtime_kind') == 'packaged'
    clock = result.get('audit_clock', {})
    clock_source = clock.get('source', {})
    assert clock_source.get('kind') == 'postgresql_clock_timestamp'
    assert clock_source.get('query') == AUDIT_CLOCK_QUERY
    assert clock_source.get('database') == DATABASE
    assert clock_source.get('clock_domain') == 'shared Docker Linux kernel CLOCK_REALTIME'
    kernel_id = clock_source.get('kernel_boot_id', '')
    assert re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', kernel_id)
    samples = clock.get('samples', [])
    assert [sample['boundary'] for sample in samples] == list(AUDIT_CLOCK_BOUNDARIES)
    timestamps = [datetime.fromisoformat(sample['timestamp']) for sample in samples]
    assert all(value.utcoffset() is not None for value in timestamps)
    assert timestamps == sorted(timestamps), 'Audit clock moved backwards across causal boundaries'
    assert result.get('process_count') == 4
    assert result.get('packaged_binary_sha256') == binary_sha256
    assert result.get('packaged_binary_sha256_after') == binary_sha256
    assert result.get('fixture_cleanup', {}).get('verified') is True
    assert result.get('internal_close_call_proven') is False
    assert result.get('packaged_caddy_shutdown_proven') is False
    cases = result.get('cases', [])
    assert [row['name'] for row in cases] == list(EXPECTED_CASES)
    assert all(row.get('success') is True and row.get('audited_recovery') is True and
               row.get('request_cleanup_verified') is True and row.get('recovery_process_cleanup_verified') is True
               for row in cases)
    graceful, expired = cases
    assert graceful['signal'] == expired['signal'] == 'SIGTERM'
    assert graceful['exit_status'] == 0 and graceful['status'] == 200
    assert graceful['exact_delete_audit_rows'] == graceful['exact_delete_audit_indexes'] == 1
    assert graceful['signal_to_blocker_release_seconds'] < 20
    assert expired['exit_status'] == 1 and expired['alive_before_drain_deadline'] is True
    assert 24.75 <= expired['signal_to_exit_seconds'] <= 31.5
    assert expired['blocker_held_through_seconds'] >= 25
    for key in ('drain_deadline_log_observed', 'backend_ended_before_blocker_release',
                'all_models_unchanged_before_blocker_release',
                'request_future_done_before_blocker_release',
                'request_transport_failure_before_blocker_release'):
        assert expired.get(key) is True, key
    assert expired['model_count_before_blocker_release'] == 128
    assert expired['activity_before_blocker_release'] == []
    assert expired['ungranted_locks_before_blocker_release'] == []
    assert expired['activity_after_blocker_release'] == []
    assert expired['late_commit_observed'] is False and expired['abandoned_audit_rows'] == 0
    assert len(processes) == 4
    for process, status in zip(processes, (0, 0, 1, 0)):
        assert process['immutable_id'] == image_id
        assert process.get('kernel_boot_id') == kernel_id
        assert process['uid'] == 10001 and process['readonly_root'] is True
        assert process['mounted_executables'] is False and process['container_pid'] == 1
        assert process['server_sha256'] == process['package_sha256'] == process['extracted_sha256'] == binary_sha256
        assert process['exit_status'] == status
        assert all(row['signal'] != 'SIGKILL' for row in process.get('signals', []))
    assert processes[0]['signals'][0]['signal'] == processes[2]['signals'][0]['signal'] == 'SIGTERM'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--go-source', type=Path, default=ROOT)
    parser.add_argument('--oracle-source', type=Path, default=ROOT)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'quality-reports/go-migration/payment-category-shutdown-image')
    args = parser.parse_args()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image):
        parser.error('--image must be an immutable image ID')
    if not re.fullmatch(r'[0-9a-f]{40}', args.expected_revision):
        parser.error('--expected-revision must be a full Git revision')
    args.go_source, args.oracle_source, args.output = (
        path.resolve() for path in (args.go_source, args.oracle_source, args.output))
    assert args.oracle_source == ROOT, 'Oracle must bind the executing checkout'
    args.output.mkdir(parents=True, exist_ok=True)
    receipt = args.output / 'results.json'
    if receipt.exists():
        parser.error('Use a fresh output directory; receipts must not be overwritten')
    report = {'success': False, 'scope': 'current-schema packaged category process shutdown',
              'expected_revision': args.expected_revision, 'packaged_checks_passed': False,
              'cleanup_verified': False, 'source_stable': False, 'build_source_stable': False,
              'complete_payment_category_parity': False, 'packaged_caddy_shutdown_proven': False,
              'internal_close_call_proven': False, 'started_at': datetime.now(timezone.utc).isoformat()}
    receipt.write_text(json.dumps(report, indent=2) + '\n')
    proof = work = build = None
    cleanup_errors = []
    started = time.monotonic()
    try:
        from TESTS.adapters.django.production_runtime import require_production_python
        report['python_runtime'] = require_production_python()
        from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
        from TESTS.adapters.django import payment_category_shutdown as oracle
        report['source_sha256'] = source_hashes(args.oracle_source)
        report['build_context_sha256'] = go_source_hashes(args.go_source)
        report['build_receipt_sha256'] = sha256(args.build_receipt)
        build = json.loads(args.build_receipt.read_text())
        binary = verify_build_receipt(build, args.go_source, args.image, args.expected_revision)
        report['build_receipt'] = build
        report['source_binding'] = {'mode': 'strict build receipt',
                                    'go_source': str(args.go_source),
                                    'oracle_source': str(args.oracle_source)}
        report['binary_sha256'] = binary
        tool_env = {key: value for key, value in os.environ.items()
                    if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
        with tempfile.TemporaryDirectory(prefix='az-category-shutdown-image-', dir='/tmp') as directory, \
                (args.output / 'services.log').open('w') as log:
            work = Path(directory)
            proof = ShutdownImageFixture(args.image, args.expected_revision, work, args.output, log, binary)
            oracle_path = args.output / 'http-results.json'
            try:
                proof.start_services(args.postgres, args.redis)
                assert proof.image_id == args.image
                initialize_current_schema(work, '127.0.0.1', proof.postgres_port, DATABASE, log)
                audit_clock = oracle.AuditClock(
                    read=proof.read_audit_time, source=proof.audit_clock_source())
                from django.test import override_settings
                cache = {'default': {'BACKEND': 'django_redis.cache.RedisCache',
                    'LOCATION': f'redis://127.0.0.1:{proof.redis_port}/1',
                    'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                                'PASSWORD': 'synthetic-identity-only'}}}
                with override_settings(CACHES=cache):
                    oracle.run_fixture(work, log, tool_env, f'127.0.0.1:{proof.redis_port}',
                        server_factory=proof.server, database_guard=proof.database_guard,
                        listener_probe=proof.listener_probe, runtime_kind='packaged',
                        audit_clock=audit_clock,
                        request_headers={'X-Forwarded-For': '127.0.0.1'}, evidence_path=oracle_path)
                report['oracle'] = json.loads(oracle_path.read_text())
                assert_oracle(report['oracle'], args.image, binary, proof.processes)
                report['packaged_checks_passed'] = True
            finally:
                try:
                    if oracle_path.exists():
                        report['oracle'] = json.loads(oracle_path.read_text())
                except BaseException as error:
                    cleanup_errors.append({'stage': 'oracle_receipt', 'error_type': type(error).__name__})
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                except BaseException as error:
                    cleanup_errors.append({'stage': 'database_connections', 'error_type': type(error).__name__})
                try:
                    proof.cleanup()
                except BaseException as error:
                    cleanup_errors.append({'stage': 'owned_containers_network', 'error_type': type(error).__name__})
    except BaseException as error:
        report['error_type'] = type(error).__name__
        # Retain source locations without exception text, locals or private fixture values.
        report['error_frames'] = [
            {'file': str(Path(frame.filename).relative_to(ROOT)),
             'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(error.__traceback__)
            if Path(frame.filename).is_relative_to(ROOT)
        ]
    finally:
        try:
            report['source_stable'] = bool(report.get('source_sha256') and
                source_hashes(args.oracle_source) == report['source_sha256'])
            report['build_source_stable'] = bool(report.get('build_context_sha256') and
                go_source_hashes(args.go_source) == report['build_context_sha256'])
            if build is not None:
                verify_build_receipt(build, args.go_source, args.image, args.expected_revision)
                assert sha256(args.build_receipt) == report['build_receipt_sha256']
        except BaseException as error:
            cleanup_errors.append({'stage': 'source_integrity', 'error_type': type(error).__name__})
        report.update(image=proof.metadata if proof else {},
                      processes=proof.processes if proof else [], cleanup_errors=cleanup_errors,
                      cleanup_verified=bool(proof and proof.cleanup_verified and not cleanup_errors),
                      private_directory_removed=bool(work and not work.exists()),
                      elapsed_seconds=round(time.monotonic() - started, 3),
                      finished_at=datetime.now(timezone.utc).isoformat())
        report['success'] = bool(report.get('packaged_checks_passed') and report['cleanup_verified']
            and report['private_directory_removed'] and report['source_stable']
            and report['build_source_stable'] and 'error_type' not in report)
        receipt.write_text(json.dumps(report, indent=2) + '\n')
    if not report['success']:
        raise SystemExit('Packaged category shutdown failed; see typed results.json receipt.')
    print(json.dumps({'success': True, 'image': args.image, 'binary_sha256': report['binary_sha256'],
                      'cases': 2, 'cleanup_verified': True}))


if __name__ == '__main__':
    main()
