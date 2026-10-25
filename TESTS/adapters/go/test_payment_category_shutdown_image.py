"""Protocol/negative controls only; these are not Docker/PostgreSQL acceptance."""
import copy
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
import inspect
import io
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from TESTS.adapters.django import payment_category_shutdown as oracle
from TESTS.adapters.go import payment_category_shutdown_image as adapter

IMAGE = 'sha256:' + 'a' * 64
BINARY = 'b' * 64
REVISION = 'c' * 40
KERNEL = '01234567-89ab-cdef-0123-456789abcdef'
HEADER = '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n'


def successful_evidence():
    graceful = {'name': oracle.EXPECTED_CASES[0], 'success': True, 'signal': 'SIGTERM',
                'exit_status': 0, 'status': 200, 'exact_delete_audit_rows': 1,
                'exact_delete_audit_indexes': 1, 'signal_to_blocker_release_seconds': .5}
    expired = {'name': oracle.EXPECTED_CASES[1], 'success': True, 'signal': 'SIGTERM',
               'exit_status': 1, 'alive_before_drain_deadline': True,
               'signal_to_exit_seconds': 25.2, 'blocker_held_through_seconds': 25.3,
               'drain_deadline_log_observed': True, 'backend_ended_before_blocker_release': True,
               'all_models_unchanged_before_blocker_release': True,
               'request_future_done_before_blocker_release': True,
               'request_transport_failure_before_blocker_release': True,
               'model_count_before_blocker_release': 128,
               'activity_before_blocker_release': [], 'ungranted_locks_before_blocker_release': [],
               'activity_after_blocker_release': [], 'late_commit_observed': False,
               'abandoned_audit_rows': 0}
    for row in (graceful, expired):
        row.update(audited_recovery=True, request_cleanup_verified=True,
                   recovery_process_cleanup_verified=True)
    result = {'success': True, 'cleanup_verified': True, 'source_stable': True,
              'fixture_snapshot_restored': True, 'packaged_binary_all_four_matches': True,
              'runtime_kind': 'packaged', 'process_count': 4,
              'packaged_binary_sha256': BINARY, 'packaged_binary_sha256_after': BINARY,
              'fixture_cleanup': {'verified': True}, 'internal_close_call_proven': False,
              'packaged_caddy_shutdown_proven': False, 'cases': [graceful, expired],
              'audit_clock': {'source': {'kind': 'postgresql_clock_timestamp',
                  'query': adapter.AUDIT_CLOCK_QUERY, 'database': adapter.DATABASE,
                  'clock_domain': 'shared Docker Linux kernel CLOCK_REALTIME',
                  'kernel_boot_id': KERNEL},
                  'samples': [{'boundary': name, 'timestamp':
                      (datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)).isoformat()}
                      for index, name in enumerate(adapter.AUDIT_CLOCK_BOUNDARIES)]}}
    processes = [{'immutable_id': IMAGE, 'uid': 10001, 'readonly_root': True,
                  'kernel_boot_id': KERNEL,
                  'mounted_executables': False, 'container_pid': 1,
                  'server_sha256': BINARY, 'package_sha256': BINARY,
                  'extracted_sha256': BINARY, 'exit_status': status,
                  'signals': [{'signal': 'SIGTERM'}]}
                 for status in (0, 0, 1, 0)]
    return result, processes


class OracleProtocolTests(unittest.TestCase):
    def test_native_api_and_real_deadline_defaults_remain(self):
        parameters = list(inspect.signature(oracle.run_fixture).parameters.values())
        self.assertEqual([p.name for p in parameters[:4]], ['work', 'log', 'tool_env', 'address'])
        for name in ('server_factory', 'database_guard', 'listener_probe', 'request_headers', 'evidence_path', 'audit_clock'):
            self.assertIs(oracle.run_fixture.__kwdefaults__[name], None)
        self.assertEqual(oracle.run_fixture.__kwdefaults__['runtime_kind'], 'native')
        self.assertEqual((oracle.DRAIN_SECONDS, oracle.EXIT_CLEANUP_TOLERANCE_SECONDS), (25, 6.5))
        self.assertEqual(len(oracle.EXPECTED_CASES), 2)
        oracle._private_database_guard(Path('/tmp/az-accounts-control'), {
            'NAME': 'assozeta_contract_accounts', 'HOST': '/tmp/az-accounts-control'})
        with self.assertRaises(AssertionError):
            oracle._private_database_guard(Path('/tmp/az-accounts-control'), {
                'NAME': 'assozeta_contract_accounts', 'HOST': '127.0.0.1'})

    def test_default_audit_clock_preserves_native_host_utc_and_records_boundaries(self):
        before = datetime.now(timezone.utc)
        clock = oracle.AuditClock()
        observed = clock('native.before_request')
        after = datetime.now(timezone.utc)
        self.assertLessEqual(before, observed)
        self.assertLessEqual(observed, after)
        self.assertEqual(clock.evidence, {
            'source': {'kind': 'host_utc', 'reader': 'datetime.now(timezone.utc)'},
            'samples': [{'boundary': 'native.before_request', 'timestamp': observed.isoformat()}]})
        self.assertIs(oracle.run.__kwdefaults__['audit_clock'], None)
        with self.assertRaises(AssertionError):
            oracle.AuditClock(read=lambda: observed)
        with self.assertRaises(AssertionError):
            oracle.AuditClock(read=lambda: datetime(2030, 1, 1), source={'kind': 'control'})('naive')

    def test_packaged_audit_clock_queries_actual_wall_time_and_records_linux_provenance(self):
        sampled = datetime(2030, 1, 1, tzinfo=timezone.utc)
        queries = []
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, query): queries.append(query)
            def fetchone(self): return (sampled,)
        database = SimpleNamespace(connection=SimpleNamespace(cursor=Cursor))
        fixture = adapter.ShutdownImageFixture(IMAGE, REVISION, Path('/tmp/control'),
                                               Path('/tmp/control'), io.StringIO(), BINARY)
        fixture.postgres = 'owned-postgres'
        with patch.object(fixture, 'docker', return_value=SimpleNamespace(stdout=KERNEL + '\n')) as docker:
            source = fixture.audit_clock_source()
        docker.assert_called_once_with('exec', 'owned-postgres', 'cat', '/proc/sys/kernel/random/boot_id')
        self.assertEqual(source['kernel_boot_id'], KERNEL)
        self.assertEqual(source['database'], adapter.DATABASE)
        with patch.dict(sys.modules, {'django.db': database}):
            clock = oracle.AuditClock(read=fixture.read_audit_time, source=source)
            self.assertEqual(clock('graceful.blocker_release_before'), sampled)
        self.assertEqual(queries, ['SELECT clock_timestamp()'])
        self.assertEqual(clock.evidence['samples'], [{'boundary': 'graceful.blocker_release_before',
                                                    'timestamp': sampled.isoformat()}])

    def test_packaged_receipt_rejects_missing_clock_samples_and_mixed_kernel_domains(self):
        result, processes = successful_evidence()
        adapter.assert_oracle(result, IMAGE, BINARY, processes)
        for field, value in [('kind', 'host_utc'), ('query', 'SELECT CURRENT_TIMESTAMP'),
                             ('database', 'other'), ('clock_domain', 'host'), ('kernel_boot_id', '')]:
            altered = copy.deepcopy(result)
            altered['audit_clock']['source'][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                adapter.assert_oracle(altered, IMAGE, BINARY, processes)
        altered = copy.deepcopy(result)
        altered['audit_clock']['samples'].pop(1)
        with self.assertRaises(AssertionError):
            adapter.assert_oracle(altered, IMAGE, BINARY, processes)
        altered = copy.deepcopy(result)
        altered['audit_clock']['samples'][1]['timestamp'] = '2029-01-01T00:00:00+00:00'
        with self.assertRaises(AssertionError):
            adapter.assert_oracle(altered, IMAGE, BINARY, processes)
        altered = copy.deepcopy(processes)
        altered[0]['kernel_boot_id'] = 'different-kernel'
        with self.assertRaises(AssertionError):
            adapter.assert_oracle(result, IMAGE, BINARY, altered)

    def test_missing_build_receipt_is_a_cli_error_before_runtime(self):
        with patch.object(sys, 'argv', ['adapter', '--image', IMAGE, '--expected-revision', REVISION]), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            adapter.main()
        self.assertEqual(raised.exception.code, 2)

    def test_receipt_requires_complete_context_and_exact_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            files = {'Dockerfile': 'FROM private\n', 'go.mod': 'module private\n', 'go.sum': '',
                     'cmd/server/main.go': 'package main\n', 'THIRD_PARTY_NOTICES.md': 'notice\n',
                     'release-manual/fixture.json': '{}'}
            for name, text in files.items():
                p = source / 'BE_GO' / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            build = {'success': True, 'source_stable': True, 'image_id': IMAGE,
                     'source_revision': REVISION, 'source_sha256': adapter.go_source_hashes(source),
                     'server_sha256': BINARY}
            self.assertEqual(adapter.verify_build_receipt(build, source, IMAGE, REVISION), BINARY)
            for key, value in [('success', False), ('source_stable', False),
                               ('image_id', 'sha256:' + 'd' * 64), ('source_revision', 'd' * 40),
                               ('server_sha256', None), ('source_sha256', {})]:
                with self.subTest(key=key), self.assertRaises(AssertionError):
                    adapter.verify_build_receipt(dict(build, **{key: value}), source, IMAGE, REVISION)
            (source / 'BE_GO/release-manual/fixture.json').write_text('{"changed":true}')
            with self.assertRaises(AssertionError):
                adapter.verify_build_receipt(build, source, IMAGE, REVISION)

    def test_runtime_keeps_one_owned_lifecycle_volume_and_strict_database_guard(self):
        with tempfile.TemporaryDirectory(prefix='az-category-shutdown-image-', dir='/tmp') as directory:
            work = Path(directory)
            proof = adapter.ShutdownImageFixture(IMAGE, REVISION, work, work, io.StringIO(), BINARY)
            proof.postgres, proof.postgres_port = 'postgres', 54321
            proof.metadata['postgres_image'] = 'sha256:' + 'e' * 64
            proof.inspect = lambda _: {'State': {'Running': True},
                'Config': {'Labels': {adapter.LABEL: proof.identifier}},
                'Image': proof.metadata['postgres_image']}
            database = {'NAME': adapter.DATABASE, 'USER': 'contracts', 'HOST': '127.0.0.1', 'PORT': 54321}
            proof.database_guard(work, database)
            for key, value in [('NAME', 'other'), ('HOST', 'production.example'), ('PORT', 54322)]:
                with self.subTest(key=key), self.assertRaises(AssertionError):
                    proof.database_guard(work, dict(database, **{key: value}))
            with patch.object(adapter.ImageFixture, 'run_container', return_value='container') as launch:
                for index in (1, 2):
                    proof.run_container('server', IMAGE, '--mount',
                        f'type=bind,source={work}/server-{index}/image-operations,target=/operations')
                    self.assertIn(f'type=bind,source={work}/shared-operations,target=/operations',
                                  launch.call_args.args)
                with self.assertRaises(AssertionError):
                    proof.run_container('server', IMAGE, '--mount', 'type=bind,source=/tmp,target=/app')

    def test_socket_observation_requires_both_families_and_actual_listen_state(self):
        closed = {'tcp': HEADER, 'tcp6': HEADER}
        self.assertTrue(adapter.listener_state(closed)['closed'])
        for family in ('tcp', 'tcp6'):
            tables = dict(closed)
            tables[family] += '0: 00000000:1F40 00000000:0000 0A 0:0 00:0 0 10001 0 123\n'
            self.assertFalse(adapter.listener_state(tables)['closed'])
            tables[family] = tables[family].replace(' 0A ', ' 01 ')
            self.assertTrue(adapter.listener_state(tables)['closed'])
        for invalid in ({'tcp': HEADER}, {'tcp': '', 'tcp6': HEADER},
                        {'tcp': HEADER + 'unreadable\n', 'tcp6': HEADER}):
            with self.subTest(invalid=invalid), self.assertRaises(AssertionError):
                adapter.listener_state(invalid)

    def test_receipt_rejects_early_exit_late_mutation_and_live_postgres_backend(self):
        result, processes = successful_evidence()
        adapter.assert_oracle(result, IMAGE, BINARY, processes)
        mutations = {'exit_status': 0, 'alive_before_drain_deadline': False,
                     'signal_to_exit_seconds': 20, 'blocker_held_through_seconds': 24.9,
                     'backend_ended_before_blocker_release': False,
                     'activity_before_blocker_release': [{'pid': 1}],
                     'ungranted_locks_before_blocker_release': [{'granted': False}],
                     'activity_after_blocker_release': [{'pid': 1}],
                     'late_commit_observed': True, 'abandoned_audit_rows': 1,
                     'model_count_before_blocker_release': 127,
                     'request_transport_failure_before_blocker_release': False,
                     'drain_deadline_log_observed': False}
        for key, value in mutations.items():
            altered = copy.deepcopy(result)
            altered['cases'][1][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                adapter.assert_oracle(altered, IMAGE, BINARY, processes)
        for key, value in [('uid', 0), ('readonly_root', False), ('mounted_executables', True),
                           ('server_sha256', 'd' * 64), ('signals', [{'signal': 'SIGKILL'}])]:
            altered = copy.deepcopy(processes)
            altered[0][key] = value
            with self.subTest(process_key=key), self.assertRaises(AssertionError):
                adapter.assert_oracle(result, IMAGE, BINARY, altered)
        with self.assertRaises(AssertionError):
            adapter.assert_oracle(result, IMAGE, BINARY, processes[:3])


class FakeDocker:
    identifier = 'private-fixture'

    def __init__(self):
        self.calls = []
        self.info = {'Id': 'container-id', 'Image': IMAGE,
                     'Config': {'Labels': {adapter.LABEL: self.identifier}},
                     'State': {'Running': True, 'Status': 'running', 'OOMKilled': False,
                               'Error': '', 'ExitCode': 0, 'FinishedAt': 'later'}}
        self.logs = ''

    def inspect(self, container):
        self.calls.append(('inspect', container))
        return self.info

    def docker(self, *args):
        self.calls.append(args)
        return SimpleNamespace(stdout=self.logs if args[0] == 'logs' else '', stderr='')


class ContainerOwnershipTests(unittest.TestCase):
    def process(self):
        fixture = FakeDocker()
        process = adapter.ContainerProcess(fixture, 'container', Path('/private/extracted-server'),
            io.StringIO(), {'host_pid': 123, 'container_id': 'container-id', 'immutable_id': IMAGE})
        return fixture, process

    def test_signal_targets_real_pid1_without_docker_stop_timeout(self):
        fixture, process = self.process()
        process.send_signal(signal.SIGTERM)
        self.assertIn(('kill', '--signal', 'TERM', 'container'), fixture.calls)
        self.assertFalse(any(call[0] == 'stop' for call in fixture.calls))
        self.assertEqual(process.identity['signals'][0]['signal'], 'SIGTERM')
        self.assertIsNone(process.poll())
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=0)

    def test_exit_wait_observes_diagnostic_and_rejects_oom_or_identity_replacement(self):
        fixture, process = self.process()
        fixture.logs = 'HTTP drain deadline exceeded\n'
        fixture.info['State'].update(Running=False, Status='exited', ExitCode=1)
        self.assertEqual(process.wait(timeout=0), 1)
        self.assertEqual(process.log.getvalue(), fixture.logs)
        process.poll()
        self.assertEqual(process.log.getvalue(), fixture.logs)
        fixture.info['State']['OOMKilled'] = True
        with self.assertRaises(AssertionError):
            process.poll()
        fixture.info['State']['OOMKilled'] = False
        fixture.info['Id'] = 'replacement'
        with self.assertRaises(AssertionError):
            process.poll()


if __name__ == '__main__':
    unittest.main()
