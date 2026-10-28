"""Offline controls only: synthetic receipts never establish image acceptance."""
import copy
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import inspect
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from TESTS.adapters.go import message_read_scopes_image as image
from TESTS.adapters.go import message_image_execution as execution
from TESTS.adapters.django import message_read_scopes_runtime as runtime
from TESTS.contracts import test_message_read_scopes as scopes
from TESTS.test_message_read_scopes import synthetic_lifecycle_receipt

IMAGE = 'sha256:' + 'a' * 64
BINARY = 'b' * 64
REVISION = 'c' * 40


def container():
    return {'Id': 'd' * 64, 'Image': IMAGE,
        'Config': {'Labels': {image.LABEL: 'owned'}, 'User': '10001:10001'},
        'State': {'Running': True, 'Pid': 321,
                  'StartedAt': '2026-10-26T01:02:03.123456789Z'}, 'RestartCount': 0}


def runtime_receipt():
    sources = image.source_hashes()
    result = {'success': True, 'runtime_executed': True, 'versioned': True,
        'errors': [], 'cleanup_errors': [], 'complete_message_family_parity': False,
        'schema': {'applied_migrations': 548, 'remaining_plan': 0, 'mode': 'full-migration-history'},
        'source_sha256': sources, 'source_sha256_at_server_start': sources,
        'source_sha256_after': sources, 'cleanup_verified': True,
        'environment_restored': True, 'private_directory_removed': True,
        'service_cleanup': dict.fromkeys(image.SERVICES, 'stopped'),
        'fixture_cleanup': {'verified': True, 'results': dict.fromkeys(runtime.FIXTURE_CLEANUP, True)},
        'resource_cleanup': {'verified': True, 'results': dict.fromkeys(image.RESOURCES, True)},
        'sensitive_cleanup': {'verified': True, 'errors': [], 'results': dict.fromkeys(image.PRIVATE_CHECKS, True)},
        'python': {'python': '3.13.16', 'sha256': 'e' * 64},
        'caddy_image_sha256': 'f' * 64,
        'misroute_control': {'rejected': True, 'actual_django_events': 1, 'state': True, 'cache': True, 'status': 200},
        'workloads': [], 'lifecycle_workloads': []}
    for label in runtime.expected_labels(True):
        result['workloads'].append({'label': label, 'success': True, 'cases': [
            {'case': case['name'], 'status': case['status'], 'check': case['check'],
             'models': 128, 'state_sha256': 'a' * 64, 'cache_sha256': 'b' * 64,
             'checks': dict.fromkeys(('response', 'state', 'cache', 'attribution'), True)}
            for case in scopes.load_source()['cases']]})
        result['lifecycle_workloads'].append(synthetic_lifecycle_receipt(label))
    return result


def parent_receipt():
    child = runtime_receipt()
    context = image.go_source_hashes(image.ROOT)
    return {'success': True, 'packaged_checks_passed': True, 'runtime': child,
        'requested_image': IMAGE, 'expected_revision': REVISION,
        'complete_message_family_parity': False, 'errors': [], 'cleanup_errors': [],
        'cleanup_verified': True, 'private_directory_removed': True,
        'source_stable': True, 'build_source_stable': True,
        'build_context_sha256': context, 'build_context_sha256_after': context,
        'build_receipt_sha256': 'e'*64,
        'build_receipt': {'success': True, 'source_stable': True, 'image_id': IMAGE,
            'source_revision': REVISION, 'server_sha256': BINARY, 'source_sha256': context},
        'image': {'immutable_id': IMAGE, 'revision': REVISION, 'server_sha256': BINARY,
            'user': '10001:10001', 'uid': 10001, 'entrypoint': ['/app/server'],
            'readonly_root': True, 'mounted_executables': False, 'platform': 'linux/arm64',
            'health_version_matches_package': True, 'version_sha256': 'f'*64},
        'third_party_license': image.license_evidence(
            (image.ROOT/'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes(),
            (image.ROOT/'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes()),
        'container': {'identity_sha256': 'd'*64,
            'process_sha256': child['lifecycle_workloads'][1]['process_sha256'],
            'restart_count': 0, 'running_verified': True, 'stopped_verified': True, 'exit_code': 0}}


class PackagedControls(unittest.TestCase):
    def setUp(self):
        # Even accidental HTTP/backend use in a portable control must fail.
        self.socket_guard = patch('socket.socket', side_effect=AssertionError('offline.socket-forbidden'))
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)

    def test_original_38_and_seven_argument_interface_preserved(self):
        self.assertEqual(len(scopes.load_source()['cases']), 38)
        self.assertEqual(list(inspect.signature(runtime.lifecycle.run_workload).parameters),
                         ['label', 'backend', 'base_url', 'fixture', 'apps', 'raw', 'observer'])
        self.assertEqual(image.EXPECTED_SCOPES, 38)
        self.assertEqual(image.EXPECTED_SEQUENCES, 2)

    def test_build_receipt_exact_context_image_revision_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in image.GO_REQUIRED_FILES + ('assets/extra.bin',):
                path = root / 'BE_GO' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture input')
            receipt = {'success': True, 'source_stable': True, 'image_id': IMAGE,
                'source_revision': REVISION, 'server_sha256': BINARY,
                'source_sha256': image.go_source_hashes(root)}
            self.assertEqual(image.verify_build_receipt(receipt, root, IMAGE, REVISION), BINARY)
            for key, value in [('success', 1), ('source_stable', False), ('image_id', 'sha256:'+'f'*64),
                    ('source_revision', 'f'*40), ('server_sha256', ''), ('source_sha256', {})]:
                bad = copy.deepcopy(receipt)
                bad[key] = value
                with self.subTest(key=key), self.assertRaises(AssertionError):
                    image.verify_build_receipt(bad, root, IMAGE, REVISION)
            for requested in ('go:latest', 'go:v1', 'sha256:short'):
                with self.assertRaises(AssertionError):
                    image.verify_build_receipt(receipt, root, requested, REVISION)
            path.write_bytes(b'changed')
            with self.assertRaises(AssertionError):
                image.verify_build_receipt(receipt, root, IMAGE, REVISION)

    def test_guard_uses_fresh_real_container_runtime_identity(self):
        value = container()
        inspector = Mock(side_effect=lambda _: copy.deepcopy(value))
        guard = image.container_liveness_guard('server', IMAGE, 'owned', inspector=inspector)
        first = guard()
        self.assertEqual(set(first), {'pid', 'instance', 'running'})
        self.assertEqual(first['pid'], 321)
        self.assertTrue(first['running'])
        self.assertEqual(guard(), first)
        self.assertEqual(inspector.call_count, 3)
        value['State']['Pid'] += 1
        self.assertFalse(guard()['running'])

    def test_container_guard_rejects_restart_exit_replacement_permission_and_foreign_ownership(self):
        for change in ('restart', 'start', 'pid', 'exit', 'id', 'image', 'owner', 'user', 'permission', 'malformed'):
            value = container()
            inspector = Mock(side_effect=lambda _: copy.deepcopy(value))
            guard = image.container_liveness_guard('server', IMAGE, 'owned', inspector=inspector)
            if change == 'restart': value['RestartCount'] += 1
            if change == 'start': value['State']['StartedAt'] = '2026-10-26T01:03:03Z'
            if change == 'pid': value['State']['Pid'] += 1
            if change == 'exit': value['State']['Running'] = False
            if change == 'id': value['Id'] = 'e'*64
            if change == 'image': value['Image'] = 'sha256:'+'e'*64
            if change == 'owner': value['Config']['Labels'][image.LABEL] = 'foreign'
            if change == 'user': value['Config']['User'] = '0'
            if change == 'permission': inspector.side_effect = PermissionError('private-secret')
            if change == 'malformed': value['State'] = None
            with self.subTest(change=change):
                self.assertFalse(guard()['running'])
                self.assertNotIn('private', json.dumps(guard()))

    def test_container_guard_cannot_establish_baseline_after_restart_or_failure(self):
        for change in ('restart', 'pid', 'exit', 'foreign', 'permission'):
            value = container()
            if change == 'restart': value['RestartCount'] = 1
            if change == 'pid': value['State']['Pid'] = True
            if change == 'exit': value['State']['Running'] = False
            if change == 'foreign': value['Config']['Labels'][image.LABEL] = 'foreign'
            inspector = Mock(return_value=value)
            if change == 'permission': inspector.side_effect = PermissionError('private')
            with self.subTest(change=change), self.assertRaises(AssertionError):
                image.container_liveness_guard('server', IMAGE, 'owned', inspector=inspector)

    def test_runtime_38_and_two_sequences_fail_closed_independently(self):
        good = runtime_receipt()
        self.assertTrue(image.runtime_acceptance(good))  # Shape control only.
        for domain in ('workloads', 'lifecycle_workloads'):
            for change in ('missing-workload', 'missing-case', 'stale-success', 'foreign-state'):
                bad = copy.deepcopy(good)
                rows = bad[domain]
                if change == 'missing-workload': rows.pop()
                elif domain == 'workloads':
                    if change == 'missing-case': rows[1]['cases'].pop()
                    if change == 'stale-success': rows[1]['cases'][0]['checks']['response'] = False
                    if change == 'foreign-state': rows[1]['cases'][0]['state_sha256'] = 'e'*64
                else:
                    if change == 'missing-case': rows[1]['scenarios'].pop()
                    if change == 'stale-success': rows[1]['scenarios'][0]['phases'][1]['status'] = 200
                    if change == 'foreign-state': rows[1]['baseline']['cache_sha256'] = 'e'*64
                with self.subTest(domain=domain, change=change):
                    self.assertFalse(image.runtime_acceptance(bad))

    def test_permission_foreign_ownership_denials_cannot_be_relabelled_success(self):
        good = runtime_receipt()
        denied = [(i, case) for i, case in enumerate(scopes.load_source()['cases']) if case['status'] != 200]
        self.assertGreater(len(denied), 0)
        for index, case in denied:
            bad = copy.deepcopy(good)
            bad['workloads'][1]['cases'][index]['status'] = 200
            with self.subTest(case=case['name']):
                self.assertFalse(image.runtime_acceptance(bad))

    def test_runtime_cleanup_source_baseline_and_misroute_are_mandatory(self):
        good = runtime_receipt()
        mutations = [lambda r: r['service_cleanup'].update(go='cleanup_failed'),
            lambda r: r['fixture_cleanup']['results'].update(cache_restored=False),
            lambda r: r['resource_cleanup'].update(verified=False),
            lambda r: r['sensitive_cleanup']['errors'].append('cleanup.failed'),
            lambda r: r.update(private_directory_removed=False),
            lambda r: r.update(source_sha256_after={}),
            lambda r: r['schema'].update(applied_migrations=128),
            lambda r: r['misroute_control'].update(rejected=False),
            lambda r: r['lifecycle_workloads'][2]['baseline'].update(state_sha256='e'*64)]
        for mutate in mutations:
            bad = copy.deepcopy(good)
            mutate(bad)
            self.assertFalse(image.runtime_acceptance(bad))

    def test_native_only_receipt_never_becomes_image_acceptance(self):
        self.assertFalse(image.acceptance(runtime_receipt()))
        self.assertFalse(runtime.acceptance(runtime_receipt()))

    def test_parent_and_runtime_decisions_remain_independent(self):
        good = parent_receipt()
        self.assertTrue(image.acceptance(good))  # Synthetic shape control only.
        mutations = [lambda r: r.update(requested_image='go:latest'),
            lambda r: r['image'].update(immutable_id='sha256:'+'f'*64),
            lambda r: r['image'].update(revision='f'*40),
            lambda r: r['image'].update(server_sha256='f'*64),
            lambda r: r['image'].update(uid=0),
            lambda r: r['third_party_license'].update(exact_bytes=False),
            lambda r: r['build_receipt'].update(source_sha256={}),
            lambda r: r['container'].update(stopped_verified=False),
            lambda r: r['container'].update(running_verified=False),
            lambda r: r['container'].update(restart_count=1),
            lambda r: r['container'].update(exit_code=137),
            lambda r: r['container'].update(process_sha256='f'*64),
            lambda r: r.update(cleanup_verified=False),
            lambda r: r.update(build_source_stable=False)]
        for mutate in mutations:
            bad = copy.deepcopy(good)
            mutate(bad)
            self.assertTrue(image.runtime_acceptance(bad['runtime']))
            self.assertFalse(image.acceptance(bad))
        bad = copy.deepcopy(good)
        bad['runtime']['workloads'][1]['cases'].pop()
        self.assertTrue(bad['packaged_checks_passed'])
        self.assertFalse(image.acceptance(bad))

    def test_source_manifest_preserves_original_inputs_and_complete_context(self):
        manifest = image.source_hashes()
        for name in ('TESTS/fixtures/message_read_scopes.json',
                'TESTS/fixtures/message_session_lifecycle.json', 'BE_GO/go.sum',
                'BE_GO/Dockerfile', 'BE_GO/THIRD_PARTY_NOTICES.md',
                'TESTS/adapters/django/message_read_scopes_runtime.py',
                'TESTS/adapters/django/message_session_lifecycle.py',
                'selfhost/caddy/Caddyfile', 'UI/Dockerfile'):
            self.assertEqual(manifest[name], image.digest(image.ROOT/name))
        self.assertTrue(set(image.go_source_hashes(image.ROOT)) <= set(manifest))

    def test_wrong_authored_responses_permission_and_foreign_ownership_rejected(self):
        source = scopes.load_source()
        tokens = {case['actor']: 'offline-token-'+case['actor'] for case in source['cases']}
        sessions = {name: 'b1000000-0000-4000-8000-'+f'{i:012d}'
                    for i, name in enumerate(image.adapter.SESSION_SPECS, 1)}
        fixture = image.adapter.runtime_manifest(source, tokens, sessions)
        original = copy.deepcopy(fixture)
        selected = [case for case in fixture['cases'] if case['status'] != 200 or 'foreign' in case['name']]
        for case in selected:
            with self.subTest(case=case['name']), \
                 patch.object(scopes.Client, 'request', return_value=(case['status'],
                     {'foreign-private-data': 'must reject'}, {'Content-Language': 'en',
                      'Content-Type': 'application/json', 'Allow': 'GET, OPTIONS'})), \
                 self.assertRaises(scopes.ResponseFailure):
                scopes.assert_case(case, 'http://offline.invalid')
        self.assertEqual(fixture, original)
        self.assertEqual(len(fixture['cases']), 38)

    def test_gateway_guard_rejects_initial_and_subsequent_restarts(self):
        value = {'Id': 'a'*64, 'State': {'Running': True, 'StartedAt': '2026-10-26T01:02:03Z'},
                 'RestartCount': 0}
        backend = lambda: {'pid': 123, 'instance': 456, 'running': True}
        with patch.object(runtime, 'inspect_owned_caddy', side_effect=lambda _: copy.deepcopy(value)):
            guard = image.gateway_liveness_guard(backend, value['Id'])
            self.assertTrue(guard()['running'])
            value['RestartCount'] = 1
            self.assertFalse(guard()['running'])
            with self.assertRaises(AssertionError):
                image.gateway_liveness_guard(backend, value['Id'])

    def test_verified_server_reads_binary_license_and_live_identity_without_popen(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            proof = image.MessageImageFixture(IMAGE, REVISION, work, work, io.StringIO())
            proof.identifier, proof.image_id, proof.redis_port = 'owned', IMAGE, 1234
            report = {'_go_source': image.ROOT}
            value = container()
            @contextmanager
            def server(*args, **kwargs):
                yield {'metadata': {'server_sha256': BINARY}, 'base_url': 'http://offline.invalid'}
                value['State'].update(Running=False, Pid=0, ExitCode=0)
            with patch.object(image.ImageFixture, 'server', new=server), \
                 patch.object(proof, 'inspect', side_effect=lambda _: copy.deepcopy(value)), \
                 patch.object(image, '_container_bytes', return_value=(image.ROOT/'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes()), \
                 patch('subprocess.Popen', side_effect=AssertionError('no Popen')):
                with proof.verified_server(work, io.StringIO(), {}, {'server_sha256': BINARY}, report) as service:
                    guard = service[runtime.LIFECYCLE_GUARD]
                    self.assertTrue(guard()['running'])
                    self.assertEqual(guard()['pid'], 321)
            self.assertTrue(report['container']['running_verified'])
            self.assertTrue(report['container']['stopped_verified'])

    def test_verified_server_rejects_wrong_binary_license_failure_and_failed_stop(self):
        for change in ('binary', 'license', 'permission', 'exit', 'restart'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                proof = image.MessageImageFixture(IMAGE, REVISION, work, work, io.StringIO())
                proof.identifier, proof.image_id, proof.redis_port = 'owned', IMAGE, 1234
                value = container()
                @contextmanager
                def server(*args, **kwargs):
                    yield {'metadata': {'server_sha256': 'f'*64 if change == 'binary' else BINARY}}
                    value['State'].update(Running=False, Pid=0, ExitCode=137 if change == 'exit' else 0)
                    if change == 'restart': value['RestartCount'] = 1
                with patch.object(image.ImageFixture, 'server', new=server), \
                     patch.object(proof, 'inspect', side_effect=PermissionError('private') if change == 'permission'
                                  else lambda _: copy.deepcopy(value)), \
                     patch.object(image, '_container_bytes', return_value=b'wrong' if change == 'license'
                                  else (image.ROOT/'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes()), \
                     self.assertRaises((AssertionError, PermissionError)):
                    with proof.verified_server(work, io.StringIO(), {}, {'server_sha256': BINARY},
                                               {'_go_source': image.ROOT}):
                        pass

    def test_owned_network_proxy_passes_real_alias_and_verifies_removal(self):
        calls = []
        @contextmanager
        def proxy(django, golang, log, **kwargs):
            calls.append((django, golang, kwargs))
            kwargs['container_observer']({'container_id': 'd'*64, 'image_id': IMAGE})
            yield 'http://offline.invalid', 'caddy:fixture'
        from TESTS.adapters import caddy
        report = {'service_cleanup': {}}
        with patch.object(caddy, 'proxy', new=proxy), \
             patch.object(image.subprocess, 'run', return_value=Mock(returncode=1)) as inspect_container:
            with image.owned_image_caddy(report, io.StringIO(), 'host.docker.internal:123',
                    'category-api:8000', network='owned-net') as result:
                self.assertEqual(result[2]['container_id'], 'd'*64)
            self.assertEqual(report['service_cleanup']['caddy'], 'stopped')
            self.assertEqual(inspect_container.call_args.args[0], ['docker', 'inspect', 'd'*64])
        self.assertEqual(calls[0][:2], ('host.docker.internal:123', 'category-api:8000'))
        self.assertEqual(calls[0][2]['network'], 'owned-net')
        self.assertNotIn('pull_policy', calls[0][2])
        with patch.object(caddy, 'proxy', new=proxy), \
             patch.object(image.subprocess, 'run', return_value=Mock(returncode=1)):
            with image.owned_image_caddy(report, io.StringIO(), 'django:8000',
                    'category-api:8000', network='owned-net', pull_policy='never'):
                pass
        self.assertEqual(calls[1][2]['pull_policy'], 'never')
        self.assertEqual(report['service_cleanup']['caddy'], 'stopped')

    def test_owned_network_proxy_cleanup_failure_or_missing_identity_rejects(self):
        from TESTS.adapters import caddy
        for change in ('cleanup', 'identity', 'body'):
            @contextmanager
            def proxy(*args, **kwargs):
                if change != 'identity':
                    kwargs['container_observer']({'container_id': 'd'*64, 'image_id': IMAGE})
                yield 'http://offline.invalid', 'caddy:fixture'
            report = {'service_cleanup': {}}
            with self.subTest(change=change), patch.object(caddy, 'proxy', new=proxy), \
                 patch.object(image.subprocess, 'run', return_value=Mock(returncode=0 if change == 'cleanup' else 1)), \
                 self.assertRaises(AssertionError):
                with image.owned_image_caddy(report, io.StringIO(), 'django:8000', 'category-api:8000',
                                             network='owned-net'):
                    if change == 'body': raise AssertionError('private-HTTP-failure')
            self.assertEqual(report['service_cleanup']['caddy'], 'cleanup_failed' if change == 'cleanup' else 'stopped')

    def test_main_failure_still_cleans_image_after_private_cleanup_error_and_preserves_inputs(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.django import production_runtime
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            original = dict(os.environ)
            build = work/'original-build.json'
            payload = json.dumps({'success': True, 'source_stable': True, 'image_id': IMAGE,
                'source_revision': REVISION, 'server_sha256': BINARY,
                'source_sha256': image.go_source_hashes(image.ROOT)}).encode()
            build.write_bytes(payload)
            output = work/'fresh'
            def start(proof, *args):
                proof.image_id = IMAGE
            with patch.object(production_runtime, 'require_production_python', return_value={'python': '3.13.16'}), \
                 patch.object(image.MessageImageFixture, 'start_services', new=start), \
                 patch.object(image, 'execute_workloads', side_effect=RuntimeError('private-token-should-not-escape')), \
                 patch.object(reuse, 'remove_sensitive_files', side_effect=PermissionError('private-path')), \
                 patch.object(image.MessageImageFixture, 'cleanup', side_effect=PermissionError('private-docker')) as cleanup, \
                 redirect_stdout(io.StringIO()):
                code = image.main(['--image', IMAGE, '--expected-revision', REVISION,
                    '--build-receipt', str(build), '--output', str(output)])
            self.assertEqual(code, 1)
            cleanup.assert_called_once()
            result = json.loads((output/'results.json').read_text())
            self.assertFalse(result['success'])
            self.assertFalse(result['runtime']['success'])
            self.assertEqual(result['errors'], ['image.execution-failed'])
            self.assertEqual(result['cleanup_errors'], ['cleanup.private-failed', 'cleanup.image-failed'])
            self.assertNotIn('private-token-should-not-escape', json.dumps(result))
            self.assertNotIn('private-path', json.dumps(result))
            self.assertNotIn('private-docker', json.dumps(result))
            self.assertEqual(build.read_bytes(), payload)
            self.assertEqual(dict(os.environ), original)
            self.assertEqual([p.name for p in output.iterdir()], ['results.json'])
            with patch.object(image.MessageImageFixture, 'start_services') as start_again, self.assertRaises(FileExistsError):
                image.main(['--image', IMAGE, '--expected-revision', REVISION,
                    '--build-receipt', str(build), '--output', str(output)])
            start_again.assert_not_called()

    def test_orchestration_uses_one_seed_and_credentials_for_all_transports_and_real_misroute(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.django import historical_read_cache as cache
        apps, observer, baseline = object(), object(), object()
        raw = Mock()
        fixture = {'tokens': {'actor': 'issued-once'}, 'sessions': {'owner': 'session-once'},
                   'runtime': {'cases': [{'name': 'original-case'}]}}
        backend_guard = lambda: {'pid': 123, 'instance': 456, 'running': True}
        go_guard = lambda: {'pid': 321, 'instance': 654, 'running': True}
        proxy_calls = []
        @contextmanager
        def django_application(report, versioned, queries, *, liveness_observer):
            self.assertIs(versioned, True)
            self.assertIsNone(queries)
            liveness_observer(backend_guard)
            yield 'http://django.invalid', observer, 9000
        @contextmanager
        def server(*args):
            yield {'base_url': 'http://go.invalid', 'proxy_upstream': 'category-api:8000',
                   runtime.LIFECYCLE_GUARD: go_guard}
        @contextmanager
        def gateway(report, log, django, golang, **kwargs):
            proxy_calls.append((django, golang, kwargs))
            yield 'http://gateway.invalid', 'caddy:fixture', {'image_id': IMAGE, 'container_id': 'd'*64}
        connections = SimpleNamespace(close_all=Mock())
        override = Mock()
        modules = {'django.db': SimpleNamespace(connections=connections),
            'django.test.utils': SimpleNamespace(override_settings=Mock(return_value=override)),
            'redis': SimpleNamespace(Redis=Mock(return_value=raw))}
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules), \
             patch.object(reuse, 'private_environment', return_value=({'PATH': '/offline'}, set())), \
             patch.object(reuse, 'apply_private_environment'), \
             patch.object(reuse, 'initialize_django_apps', return_value=(apps, None)), \
             patch.object(reuse, 'migrate_full_schema', return_value={
                 'applied_migrations': 548, 'remaining_plan': 0, 'mode': 'full-migration-history'}), \
             patch.object(image.adapter, 'snapshot_state', return_value=baseline), \
             patch.object(cache, 'snapshot_historical_read_cache', return_value=baseline), \
             patch.object(image.adapter, 'seed_fixture', return_value=fixture) as seed, \
             patch.object(image.adapter.shared, 'delete_created', return_value=True), \
             patch.object(image.adapter, 'restore_cache', return_value=True), \
             patch.object(reuse, 'django_application', new=django_application), \
             patch.object(runtime, 'run_workloads') as workloads, \
             patch.object(image, 'gateway_liveness_guard', return_value=backend_guard), \
             patch.object(image, 'owned_image_caddy', new=gateway), \
             patch.object(reuse, 'owned_caddy', new=gateway), \
             patch.object(runtime, 'misroute_control', return_value={'rejected': True}) as misroute, \
             patch('TESTS.http_client.poll'):
            proof = SimpleNamespace(redis_port=1234, postgres_port=5432, network='owned-net', verified_server=server)
            report = {'runtime': {'workloads': [], 'lifecycle_workloads': [],
                                 'service_cleanup': dict.fromkeys(image.SERVICES, 'not_started')}}
            secrets = set()
            image.execute_workloads(reuse, proof, Path(directory), io.StringIO(), {}, {}, report, secrets)
        seed.assert_called_once()
        self.assertEqual([call.args[0] for call in workloads.call_args_list], list(runtime.expected_labels(True)))
        self.assertEqual([call.args[1] for call in workloads.call_args_list], ['django', 'go', 'django', 'go'])
        for call in workloads.call_args_list:
            self.assertIs(call.args[3], fixture)
            self.assertIs(call.args[4], apps)
            self.assertIs(call.args[5], raw)
            self.assertIs(call.args[6], observer)
        self.assertEqual(proxy_calls[0], ('host.docker.internal:9000', 'category-api:8000', {'network': 'owned-net'}))
        self.assertEqual(proxy_calls[1], ('host.docker.internal:9000', 'host.docker.internal:9000', {'service': 'caddy-misroute'}))
        self.assertEqual(misroute.call_args.args[1], 'http://gateway.invalid/api/v2')
        self.assertIs(misroute.call_args.args[3], fixture)
        self.assertEqual(secrets, {'issued-once', 'session-once'})
        self.assertTrue(report['runtime']['runtime_executed'])
        self.assertTrue(report['runtime']['fixture_cleanup']['verified'])
        modules['redis'].Redis.assert_called_once_with(host='127.0.0.1', port=1234, db=1,
            password='synthetic-identity-only', decode_responses=False,
            socket_timeout=5, socket_connect_timeout=5)

    def test_optimized_python_rejected_before_output_or_services(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'untouched'
            result = subprocess.run([sys.executable, '-O', '-m',
                'TESTS.adapters.go.message_read_scopes_image', '--image', IMAGE,
                '--expected-revision', REVISION, '--build-receipt', 'missing', '--output', str(output)],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertIn('optimized-python', result.stderr)

    def test_mutable_tag_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'untouched'
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                image.main(['--image', 'go:latest', '--expected-revision', REVISION,
                            '--build-receipt', 'missing', '--output', str(output)])
            self.assertFalse(output.exists())


class CleanupCancellationControls(unittest.TestCase):
    """Actual runner teardown control flow; all service operations are offline."""

    def exercise(self, boundary, sig, *, business_index=1, removal_failure=False,
                 startup_failure=False):
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.django import historical_read_cache as cache

        attempted, finished, timers, handlers, proofs = [], [], [], {}, []
        delivered = False

        def operation(name):
            nonlocal delivered
            attempted.append(name)
            if name == boundary and not delivered and (
                    name != 'business' or attempted.count('business') == business_index):
                delivered = True
                handlers[sig](sig, None)
                # Repeated external cancellation cannot abort this operation.
                handlers[signal.SIGINT](signal.SIGINT, None)
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            finished.append(name)
            return True

        raw = Mock()
        raw.close.side_effect = lambda: operation('redis_client')
        connections = SimpleNamespace(close_all=lambda: operation('db_connections'))
        override = Mock()
        override.disable.side_effect = lambda: operation('cache_override')
        modules = {'django.db': SimpleNamespace(connections=connections),
            'django.test.utils': SimpleNamespace(override_settings=Mock(return_value=override)),
            'redis': SimpleNamespace(Redis=Mock(return_value=raw))}
        fixture = {'tokens': {}, 'sessions': {}, 'runtime': {'cases': [{}]}}
        guard = lambda: {'pid': 123, 'instance': 456, 'running': True}

        @contextmanager
        def django_application(*args, liveness_observer, **kwargs):
            liveness_observer(guard)
            yield 'http://offline.invalid', object(), 9000

        @contextmanager
        def server(*args):
            yield {'base_url': 'http://offline.invalid', 'proxy_upstream': 'offline:8000',
                   runtime.LIFECYCLE_GUARD: guard}

        @contextmanager
        def gateway(*args, **kwargs):
            yield 'http://offline.invalid', 'fixture', {'image_id': IMAGE, 'container_id': 'd' * 64}

        def start(proof, *args):
            proofs.append(proof)
            proof.image_id, proof.redis_port, proof.postgres_port = IMAGE, 1234, 5432
            proof.containers = ['owned-postgres', 'owned-redis', 'owned-server']
            proof.network_created = True
            (proof.output / 'services.log').touch()

            def docker(*args, **kwargs):
                if args[0] == 'inspect':
                    operation('image_inspect')
                    payload = [{'Config': {'Labels': {image.LABEL: proof.identifier}}}]
                elif args[:2] == ('network', 'inspect'):
                    payload = [{'Labels': {image.LABEL: proof.identifier}}]
                elif args[0] == 'rm':
                    operation(args[-1])
                    payload = None
                elif args[:2] == ('network', 'rm'):
                    operation('owned-network')
                    payload = None
                else:
                    payload = None
                failed = removal_failure and args[0] == 'rm' and args[-1] == 'owned-server'
                return SimpleNamespace(returncode=int(failed),
                    stdout=json.dumps(payload) if payload else '', stderr='')
            proof.docker = docker
            if startup_failure:
                raise RuntimeError('offline partial startup')

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            work = Path(directory)
            output = work / 'fresh'
            build = work / 'build.json'
            build.write_text(json.dumps({'success': True, 'source_stable': True, 'image_id': IMAGE,
                'source_revision': REVISION, 'server_sha256': BINARY, 'source_sha256': {}}))
            replacements = (
                (execution.signal, 'signal', lambda number, handler: handlers.update({number: handler})),
                (execution.signal, 'getsignal', lambda _: signal.SIG_DFL),
                (execution.signal, 'getitimer', lambda _: (0.0, 0.0)),
                (execution.signal, 'setitimer', lambda *args: timers.append(args)),
                (image, 'source_hashes', lambda: {}), (image, 'go_source_hashes', lambda _: {}),
                (image.MessageImageFixture, 'start_services', start),
                (image.MessageImageFixture, 'verified_server', server),
                (reuse, 'private_environment', lambda *args: ({}, set())),
                (reuse, 'apply_private_environment', lambda _: None),
                (reuse, 'initialize_django_apps', lambda: (object(), None)),
                (reuse, 'cache_override', lambda _: {}),
                (reuse, 'migrate_full_schema', lambda _: {'applied_migrations': 548,
                    'remaining_plan': 0, 'mode': 'full-migration-history'}),
                (image.adapter, 'snapshot_state', lambda _: 'baseline'),
                (cache, 'snapshot_historical_read_cache', lambda _: 'baseline'),
                (image.adapter, 'seed_fixture', lambda *args: fixture),
                (image.adapter, 'remove_private_fixture', lambda _: operation('private_fixture')),
                (image.adapter.shared, 'delete_created', lambda _: operation('fixture_rows')),
                (image.adapter, 'restore_cache', lambda *args: operation('cache_restored')),
                (reuse, 'django_application', django_application),
                (runtime, 'run_workloads', lambda *args: operation('business')),
                (image, 'gateway_liveness_guard', lambda *args: guard),
                (image, 'owned_image_caddy', gateway), (reuse, 'owned_caddy', gateway),
                (runtime, 'misroute_control', lambda *args: {'rejected': True}),
                (reuse, 'remove_sensitive_files', lambda *args: operation('sensitive_files')),
                # Isolate cancellation's failure override from acceptance schema.
                (image, 'acceptance', lambda _: True), (image, 'runtime_acceptance', lambda _: True),
            )
            for target, name, replacement in replacements:
                stack.enter_context(patch.object(target, name, new=replacement))
            stack.enter_context(patch.dict(sys.modules, modules))
            stack.enter_context(patch('TESTS.http_client.poll'))
            stack.enter_context(patch('socket.socket', side_effect=AssertionError('offline socket')))
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('offline process')))
            stack.enter_context(redirect_stdout(io.StringIO()))
            code = execution.run_packaged(['--image', IMAGE, '--expected-revision', REVISION,
                '--build-receipt', str(build), '--output', str(output)],
                deadline=20, cleanup_grace=7, runner=image.main)
            result = json.loads((output / 'results.json').read_text())
            self.assertTrue(delivered, attempted)
            self.assertEqual(code, 1)
            self.assertFalse(result['success'])
            self.assertTrue(result['execution']['interrupted'])
            self.assertEqual(result['execution']['reason'],
                {signal.SIGALRM: 'deadline', signal.SIGINT: 'sigint', signal.SIGTERM: 'sigterm'}[sig])
            self.assertFalse(result['execution']['escalated'])
            self.assertEqual([name for name in attempted if name.startswith('owned-')],
                             ['owned-server', 'owned-redis', 'owned-postgres', 'owned-network'])
            self.assertEqual([name for name in finished if name.startswith('owned-')],
                             ['owned-server', 'owned-redis', 'owned-postgres', 'owned-network'])
            self.assertEqual(proofs[0].cleanup_verified, not removal_failure)
            self.assertEqual(timers, [(signal.ITIMER_REAL, 20), (signal.ITIMER_REAL, 7),
                                      (signal.ITIMER_REAL, 0)])
            self.assertTrue(result['private_directory_removed'])
            self.assertEqual(result['cleanup_verified'], not removal_failure)
            self.assertEqual(result['cleanup_errors'], ['cleanup.image-failed'] if removal_failure else [])
            if startup_failure:
                self.assertNotIn('business', attempted)
                self.assertEqual(result['errors'], ['image.execution-failed', 'execution.interrupted'])
            elif boundary == 'business':
                self.assertEqual(attempted.count('business'), business_index)
                self.assertEqual(finished.count('business'), business_index - 1)
                self.assertIn('image.execution-failed', result['errors'])
            else:
                self.assertEqual(attempted.count('business'), 4)
                self.assertEqual(result['errors'], ['execution.interrupted'])
                self.assertTrue(result['runtime']['fixture_cleanup']['verified'])
                self.assertTrue(result['runtime']['resource_cleanup']['verified'])
            expected = ('sensitive_files',) if startup_failure else (
                'private_fixture', 'fixture_rows', 'cache_restored', 'redis_client',
                'cache_override', 'sensitive_files')
            for name in expected:
                self.assertIn(name, finished)
            return result

    def test_first_cancellation_in_each_terminal_cleanup_finishes_all_removal_attempts(self):
        for boundary in ('private_fixture', 'redis_client', 'sensitive_files', 'image_inspect'):
            for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
                with self.subTest(boundary=boundary, sig=sig):
                    self.exercise(boundary, sig)

    def test_business_cancellation_still_unwinds_before_remaining_workloads(self):
        for index in range(1, 5):
            for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
                with self.subTest(index=index, sig=sig):
                    self.exercise('business', sig, business_index=index)

    def test_first_cancellation_in_outer_cleanup_after_partial_startup_finishes_removals(self):
        for boundary in ('sensitive_files', 'image_inspect'):
            for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
                with self.subTest(boundary=boundary, sig=sig):
                    self.exercise(boundary, sig, startup_failure=True)

    def test_cleanup_cancellation_preserves_removal_failure_and_attempts_later_resources(self):
        for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig=sig):
                self.exercise('image_inspect', sig, removal_failure=True)


if __name__ == '__main__':
    unittest.main()
