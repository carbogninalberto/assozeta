"""Offline rejection controls; these tests never claim live acceptance."""
import copy
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import MappingProxyType, SimpleNamespace
from contextlib import contextmanager

from TESTS.contracts import test_message_read_scopes as contract
from TESTS.adapters.django import message_read_scopes as adapter
from TESTS.adapters.django import message_read_scopes_runtime as runtime
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_session_lifecycle as lifecycle
from TESTS.contracts import test_message_session_lifecycle as lifecycle_contract
from TESTS.adapters.django import historical_read_cache as cache


def cache_fixture():
    contrasts = cache.HistoricalReadCacheContrasts(cache.PERSISTENT_CONTRAST_KEY,
        cache.EXPIRING_CONTRAST_KEY, 4000000000000, b'persistent-dump', b'expiring-dump')
    entries = {
        contrasts.persistent_key: cache.HistoricalReadCacheEntry(b'string', b'persistent-dump', -1),
        contrasts.expiring_key: cache.HistoricalReadCacheEntry(b'string', b'expiring-dump', contrasts.expires_at_ms),
        b':1:impersonation:v1:private': cache.HistoricalReadCacheEntry(b'string', b'session-dump', 4000000000000),
    }
    return cache.HistoricalReadCacheSnapshot(MappingProxyType(entries)), contrasts


def synthetic_lifecycle_receipt(label):
    """Shape control only; this provides no actual runtime/process evidence."""
    source = lifecycle_contract.load_source()
    baseline = {'models': 128, 'cache_keys': 4, 'state_sha256': 'a' * 64, 'cache_sha256': 'b' * 64}
    report = {'label': label, 'backend': lifecycle_contract.LABELS[label],
        'source_sha256': lifecycle_contract.source_hashes(),
        'manifest_sha256': lifecycle_contract.source_digest(source), 'success': True, 'failure': None,
        'baseline': baseline, 'process_sha256': 'c' * 64, 'scenarios': [],
        'restoration': lifecycle_contract.restoration_receipt(baseline)}
    for scenario in source['scenarios']:
        result = {'scenario': scenario['name'], 'process_sha256': report['process_sha256'],
            'phases': [], 'transition': {'code': scenario['transition'], 'removed_keys': 1,
                'unchanged_keys': 3, 'models': 128, 'state_sha256': baseline['state_sha256'],
                'cache_sha256': 'd' * 64, 'checks': dict.fromkeys(('state', 'cache', 'process'), True)},
            'restoration': lifecycle_contract.restoration_receipt(baseline)}
        for index, phase in enumerate(scenario['phases']):
            result['phases'].append({'phase': 'denied' if index else 'warm', 'case': phase['name'],
                'status': phase['status'], 'check': phase['check'],
                'checks': dict.fromkeys(('response', 'state', 'cache', 'attribution', 'process'), True),
                'models': 128, 'state_sha256': baseline['state_sha256'],
                'cache_sha256': 'd' * 64 if index else baseline['cache_sha256'],
                'authored_sha256': lifecycle_contract.source_digest(phase),
                'process_sha256': report['process_sha256']})
        report['scenarios'].append(result)
    return report


class LifecycleGuardControls(unittest.TestCase):
    """Offline handles and injected inspection; never actual process proof."""

    def process(self):
        process = SimpleNamespace(pid=123, returncode=None)
        process.poll = Mock(side_effect=lambda: process.returncode)
        return process

    def container(self):
        return {'Id': 'a' * 64, 'State': {'Running': True, 'StartedAt': '2026-10-26T01:02:03.123456789Z'},
                'RestartCount': 0}

    def test_native_guard_retains_observed_process_and_detects_exit_or_pid_change(self):
        process = self.process()
        guard = runtime.go_liveness_guard(process)
        self.assertEqual(guard(), {'pid': process.pid, 'instance': id(process), 'running': True})
        process.returncode = 0
        self.assertFalse(guard()['running'])
        process.returncode, process.pid = None, 124
        self.assertFalse(guard()['running'])
        self.assertGreaterEqual(process.poll.call_count, 2)

    def test_caddy_composes_backend_and_each_live_inspection_without_exposing_identity(self):
        backend = runtime.go_liveness_guard(self.process())
        inspection = self.container()
        inspector = Mock(side_effect=lambda value: copy.deepcopy(inspection))
        guard = runtime.caddy_liveness_guard(backend, inspection['Id'], inspector=inspector)
        evidence = guard()
        self.assertTrue(evidence['running'])
        self.assertEqual(evidence['pid'], backend()['pid'])
        self.assertNotEqual(evidence['instance'], backend()['instance'])
        self.assertGreater(evidence['instance'], 0)
        self.assertEqual(inspector.call_count, 2)
        self.assertEqual(set(evidence), {'pid', 'instance', 'running'})
        self.assertEqual(guard(), evidence)
        other = runtime.caddy_liveness_guard(backend, inspection['Id'], inspector=lambda _: {
            **inspection, 'RestartCount': 1})
        self.assertNotEqual(other()['instance'], evidence['instance'])

    def test_caddy_rejects_death_restart_replacement_and_inspector_failure(self):
        for change in ('id', 'running', 'started', 'restart', 'backend', 'pid', 'exception', 'malformed'):
            process, inspection = self.process(), self.container()
            inspector = Mock(side_effect=lambda _: copy.deepcopy(inspection))
            guard = runtime.caddy_liveness_guard(runtime.go_liveness_guard(process), inspection['Id'], inspector=inspector)
            if change == 'id': inspection['Id'] = 'b' * 64
            if change == 'running': inspection['State']['Running'] = False
            if change == 'started': inspection['State']['StartedAt'] = '2026-10-26T01:03:03Z'
            if change == 'restart': inspection['RestartCount'] += 1
            if change == 'backend': process.returncode = 0
            if change == 'pid': process.pid += 1
            if change == 'exception': inspector.side_effect = RuntimeError('private-docker-output')
            if change == 'malformed': inspection['State'] = None
            with self.subTest(change=change):
                result = guard()
                self.assertIs(result['running'], False)
                self.assertNotIn('private', json.dumps(result))

    def test_caddy_constructor_fails_closed_on_partial_or_invalid_setup(self):
        for change in ('id', 'missing', 'not-running', 'start', 'restart', 'inspect', 'backend'):
            inspection = self.container()
            backend = runtime.go_liveness_guard(self.process())
            inspector = Mock(return_value=inspection)
            if change == 'id': inspection['Id'] = 'b' * 64
            if change == 'missing': inspection.pop('State')
            if change == 'not-running': inspection['State']['Running'] = False
            if change == 'start': inspection['State']['StartedAt'] = ''
            if change == 'restart': inspection['RestartCount'] = True
            if change == 'inspect': inspector.side_effect = RuntimeError('private-inspect-error')
            if change == 'backend': backend = None
            with self.subTest(change=change), self.assertRaisesRegex(AssertionError, '^lifecycle.gateway-guard$'):
                runtime.caddy_liveness_guard(backend, 'a' * 64, inspector=inspector)

    def test_default_caddy_inspector_uses_bounded_argv_and_sanitized_environment(self):
        inspection = self.container()
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(inspection).encode())
        with patch.dict(os.environ, {'PATH': '/owned/tools', 'DOCKER_HOST': 'private',
                                     'AWS_SECRET_ACCESS_KEY': 'private'}, clear=True), \
             patch.object(runtime.subprocess, 'run', return_value=completed) as run:
            guard = runtime.caddy_liveness_guard(runtime.go_liveness_guard(self.process()), inspection['Id'])
            self.assertTrue(guard()['running'])
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            argv = call.args[0]
            self.assertEqual(argv[:5], ['docker', 'inspect', '--type', 'container', '--format'])
            self.assertEqual(argv[-1], inspection['Id'])
            self.assertIn('StartedAt', argv[5])
            self.assertIn('RestartCount', argv[5])
            self.assertEqual(call.kwargs['env'], {'PATH': '/owned/tools'})
            self.assertEqual(call.kwargs['timeout'], 5)
            self.assertEqual(call.kwargs['stderr'], subprocess.DEVNULL)
            self.assertFalse(call.kwargs.get('shell', False))
        with patch.object(runtime.subprocess, 'run') as run, self.assertRaises(AssertionError):
            runtime.inspect_owned_caddy('invalid; private')
        run.assert_not_called()

    def test_actual_inspection_boundary_rejects_timeout_nonzero_invalid_or_oversize_json(self):
        good = SimpleNamespace(returncode=0, stdout=json.dumps(self.container()).encode())
        for bad in (SimpleNamespace(returncode=1, stdout=b'private-docker-error'),
                    SimpleNamespace(returncode=0, stdout=b'private-invalid-json'),
                    SimpleNamespace(returncode=0, stdout=b' ' * 8193),
                    SimpleNamespace(returncode=0, stdout=b'{}'),
                    subprocess.TimeoutExpired('docker', 5, output=b'private-docker-error')):
            with self.subTest(bad=type(bad).__name__), \
                 patch.object(runtime.subprocess, 'run', return_value=good) as run:
                backend = runtime.go_liveness_guard(self.process())
                guard = runtime.caddy_liveness_guard(backend, 'a' * 64)
                run.side_effect = bad if isinstance(bad, BaseException) else None
                run.return_value = bad
                self.assertFalse(guard()['running'])
                with self.assertRaisesRegex(AssertionError, '^lifecycle.gateway-guard$') as caught:
                    runtime.caddy_liveness_guard(backend, 'a' * 64)
                self.assertNotIn('private', str(caught.exception))

    def test_go_context_yields_copy_with_guard_and_closes_on_success_failure_or_missing_observation(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.go import payment_category_server
        for failure in (None, 'body', 'observation', 'setup'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                executable = work / 'server'
                executable.write_bytes(b'offline-artifact')
                executable.with_name('image-codec').write_bytes(b'offline-codec')
                process = self.process()
                process.args = [str(executable)]
                native = {'base_url': 'offline', 'proxy_upstream': 'offline-upstream', 'metadata': {}}
                events, guards = [], []
                @contextmanager
                def factory(*args, process_observer, **kwargs):
                    if failure != 'observation':
                        process_observer(process)
                    try:
                        if failure == 'setup':
                            raise RuntimeError('offline-setup')
                        yield native
                    finally:
                        events.append('factory-cleanup')
                        process.returncode = 0
                report = {'service_cleanup': {}}
                with patch.object(payment_category_server, 'native_server', new=factory), \
                     patch('socket.socket', side_effect=AssertionError('socket forbidden')):
                    try:
                        with runtime.go_service(reuse, report, work, io.StringIO(), {}, 1, False) as result:
                            self.assertIsNot(result, native)
                            self.assertEqual(set(result), set(native) | {runtime.LIFECYCLE_GUARD})
                            self.assertNotIn(runtime.LIFECYCLE_GUARD, native)
                            guard = result[runtime.LIFECYCLE_GUARD]
                            self.assertEqual(guard()['instance'], id(process))
                            self.assertTrue(guard()['running'])
                            guards.append(guard)
                            if failure == 'body':
                                raise RuntimeError('offline-body')
                    except (RuntimeError, AssertionError) as error:
                        if failure is None:
                            raise
                        self.assertIs(type(error), AssertionError if failure == 'observation' else RuntimeError)
                        self.assertEqual(str(error), 'go.process' if failure == 'observation' else 'offline-' + failure)
                    else:
                        self.assertIsNone(failure)
                self.assertEqual(events, ['factory-cleanup'])
                if failure == 'observation':
                    self.assertEqual(report['service_cleanup']['go'], 'start_attempted')
                else:
                    self.assertEqual(report['service_cleanup']['go'], 'stopped')
                    self.assertEqual(report['go_artifact']['process_exit'], 0)
                for guard in guards:
                    self.assertFalse(guard()['running'])

    def test_failed_lifecycle_receipt_is_recorded_before_fixed_failure(self):
        fixture = {'private': object()}
        report = {'workloads': [], 'lifecycle_workloads': []}
        failed = {'label': 'django-native', 'success': False}
        with patch.object(adapter, 'run_workload') as scopes_workload, \
             patch.object(lifecycle, 'run_workload', return_value=failed) as lifecycle_workload:
            with self.assertRaisesRegex(AssertionError, '^lifecycle.workload-failed$'):
                runtime.run_workloads('django-native', 'django', 'offline', fixture, None, None, None,
                                      lambda: None, report)
        self.assertEqual(report['lifecycle_workloads'], [failed])
        scopes_workload.assert_called_once()
        lifecycle_workload.assert_called_once()
        self.assertNotIn('message_session_lifecycle_process', fixture)


class ManifestControls(unittest.TestCase):
    def setUp(self):
        self.source = contract.load_source()
        self.tokens = {case['actor']: 'private-token-' + case['actor'] for case in self.source['cases']}
        self.sessions = {label: 'b1000000-0000-4000-8000-' + f'{index:012d}'
                         for index, label in enumerate(adapter.SESSION_SPECS, 1)}
        self.converted = adapter.runtime_manifest(self.source, self.tokens, self.sessions)

    def test_source_complete_distinct_from_original_matrix(self):
        original = json.loads((adapter.ROOT / 'TESTS/fixtures/message_reads.json').read_text())
        self.assertEqual(len(self.source['cases']), 38)
        self.assertFalse(set(contract.CASE_NAMES) & {case['name'] for case in original['cases']})
        self.assertTrue(all(case['reason'] and case['check'] for case in self.source['cases']))
        self.assertEqual(self.source['read_effects'], {'managed_models': 128, 'audit_rows_created': 0,
                         'session_writes': 0, 'cache_exclusions': []})

    def test_credential_projection_preserves_all_authored_expectations(self):
        contract.validate_runtime(self.converted, self.source)
        for source, converted in zip(self.source['cases'], self.converted['cases'], strict=True):
            self.assertEqual(source['response'], converted['response'])
            self.assertNotIn('actor', converted)
            self.assertEqual(converted['token'], self.tokens[source['actor']])

    def test_missing_duplicate_reordered_and_modified_case_fail(self):
        for change in ('missing', 'duplicate', 'order', 'body', 'status', 'scope', 'path', 'check', 'extra', 'source'):
            changed = copy.deepcopy(self.converted)
            if change == 'missing': changed['cases'].pop()
            if change == 'duplicate': changed['cases'][-1] = changed['cases'][0]
            if change == 'order': changed['cases'].reverse()
            if change == 'body': changed['cases'][0]['response'] = []
            if change == 'status': changed['cases'][0]['status'] = 403
            if change == 'scope': changed['cases'][0]['scope'] = 'admin'
            if change == 'path': changed['cases'][0]['path'] += '?unexpected=1'
            if change == 'check': changed['cases'][0]['check'] = 'other'
            if change == 'extra': changed['cases'][0]['unexpected'] = True
            if change == 'source': changed['source_sha256'] = '0' * 64
            with self.subTest(change=change), self.assertRaises(AssertionError):
                contract.validate_runtime(changed, self.source)

    def test_tokens_sessions_and_literal_headers_are_closed(self):
        for change in ('token', 'actor', 'session', 'inconsistent', 'header'):
            changed = copy.deepcopy(self.converted)
            if change == 'token': changed['cases'][0]['token'] = ''
            if change == 'actor': changed['cases'][0]['actor'] = 'admin'
            if change == 'session': changed['cases'][0]['headers']['X-Impersonation-Id'] = 'broken'
            if change == 'inconsistent': changed['cases'][1]['headers']['X-Impersonation-Id'] = self.sessions['admin_foreign']
            if change == 'header': changed['cases'][22]['headers']['User-Id'] = self.source['ids']['owner']
            with self.subTest(change=change), self.assertRaises(AssertionError):
                contract.validate_runtime(changed, self.source)

    def test_auth_projection_distinguishes_supported_targets_and_scope(self):
        by_name = {case['name'].removeprefix('message-scopes-'): case for case in self.source['cases']}
        self.assertEqual(by_name['admin-full-detail']['effective'], 'full_collaborator')
        self.assertEqual(by_name['admin-full-detail']['scope'], 'owner')
        self.assertEqual(by_name['owner-target-owner-ineligible']['status'], 403)
        self.assertEqual(by_name['owner-athlete-list']['check'], 'denied.association-operation')
        self.assertEqual(by_name['admin-athlete-list']['check'], 'denied.association-role')
        self.assertEqual(by_name['owner-full-header-foreign-group']['status'], 200)
        self.assertEqual(by_name['owner-full-query-foreign-group']['status'], 403)
        self.assertEqual(by_name['admin-owner-query-foreign-group']['status'], 200)
        for case in self.source['cases']:
            if case['check'] == 'denied.invalid-session':
                self.assertIsNone(case['effective'])
                self.assertIsNone(case['scope'])

    def test_contract_never_imports_backend(self):
        import ast
        tree = ast.parse(Path(contract.__file__).read_text())
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): modules.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom): modules.append(node.module or '')
        self.assertFalse(any(name.startswith(('django', 'application', 'BE_GO', 'TESTS.adapters')) for name in modules))

    def test_explicit_nested_expectations_are_distinct_despite_shared_recipient(self):
        by_name = {case['name'].removeprefix('message-scopes-'): case for case in self.source['cases']}
        own = by_name['admin-owner-detail']['response']
        foreign = by_name['direct-foreign-detail']['response']
        self.assertEqual(own[0]['recipient'], foreign[0]['recipient'])
        self.assertNotEqual(own[0]['message'], foreign[0]['message'])
        self.assertEqual(by_name['nested-own-message-foreign-delivery']['response'], own)
        self.assertEqual(by_name['nested-foreign-message-own-delivery']['status'], 404)
        self.assertNotEqual(by_name['admin-owner-original-detail']['path'],
                            by_name['admin-owner-foreign-detail']['path'])

    def test_http_body_mismatch_uses_fixed_code_without_private_payload(self):
        case = self.converted['cases'][0]
        with patch.object(contract.Client, 'request', return_value=(200, {'token': 'private-value'}, {})):
            with self.assertRaisesRegex(AssertionError, '^response.body$'):
                contract.assert_case(case, 'http://offline.invalid')

    def test_redirect_to_expected_body_fails_contract_without_second_request(self):
        from TESTS.test_http_client import fake_http_transport
        case = self.converted['cases'][0]
        for status in (301, 302, 303, 307, 308):
            for location in ('/next', 'http://outside.invalid/next'):
                with self.subTest(status=status, location=location):
                    with fake_http_transport(status, location, headers={
                            'Content-Language': 'en', 'Content-Type': 'application/json',
                            'Allow': 'GET, OPTIONS'}, followed_body=json.dumps(case['response']).encode()) as calls:
                        with self.assertRaises(contract.ResponseFailure) as caught:
                            contract.assert_case(case, 'http://owned.invalid')
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(adapter.failure_diagnostic(caught.exception),
                                     {'check': 'response.status', 'status': status})


class RuntimeControls(unittest.TestCase):
    def adversarial_build_environment(self):
        environment = {
            'PATH': '/provisioned/bin', 'HOME': '/provisioned/home',
            'GOCACHE': '/provisioned/cache', 'GOMODCACHE': '/provisioned/modules',
            'GOPATH': '/provisioned/gopath', 'TMPDIR': '/private/tmp',
            'GOFLAGS': '-overlay=/outside/overlay.json -tags=outside -modfile=/outside/go.mod -compiler=gccgo -toolexec=/outside/compiler',
            'GOWORK': '/outside/go.work', 'GOENV': '/outside/go.env', 'GOTOOLCHAIN': '/outside/toolchain',
            'GOPROXY': 'https://outside.invalid', 'GOSUMDB': 'outside.invalid',
        }
        for name in ('CC', 'CXX', 'GCCGO', 'GOROOT', 'GOTOOLDIR', 'GOEXPERIMENT',
                     'GOOS', 'GOARCH', 'GOAMD64', 'CGO_ENABLED', 'CGO_CFLAGS',
                     'CGO_CPPFLAGS', 'CGO_CXXFLAGS', 'CGO_LDFLAGS', 'CFLAGS', 'CPPFLAGS',
                     'LDFLAGS', 'PKG_CONFIG', 'PKG_CONFIG_PATH'):
            environment[name] = '/outside/' + name
        return environment

    def assert_owned_build_environment(self, environment):
        self.assertEqual(environment['GOFLAGS'], '-p=1')
        for key, value in {'GOWORK': 'off', 'GOENV': 'off', 'GOTOOLCHAIN': 'local',
                           'GOPROXY': 'off', 'GOSUMDB': 'off'}.items():
            self.assertEqual(environment.get(key), value)
        for key, value in self.adversarial_build_environment().items():
            if '/outside/' in value or 'outside.invalid' in value:
                self.assertNotEqual(environment.get(key), value, key)
            else:
                self.assertEqual(environment.get(key), value, key)
        self.assertNotIn('/outside/', json.dumps(environment))
        self.assertEqual(set(environment), {'PATH', 'HOME', 'GOCACHE', 'GOMODCACHE', 'GOPATH', 'TMPDIR',
                         'GOFLAGS', 'GOWORK', 'GOENV', 'GOTOOLCHAIN', 'GOPROXY', 'GOSUMDB', 'LANG', 'LC_ALL'})

    def test_adversarial_environment_cannot_reach_real_native_build_boundary(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.go import payment_category_server  # Import before installing socket guard.
        class BuildBoundaryReached(Exception):
            pass
        for forwarded in ('sanitized', 'adversarial'):
            calls = []
            def pkg_config(command, **kwargs):
                calls.append((command, kwargs['env'].copy(), kwargs.get('cwd')))
                return '-I/provisioned/include -L/provisioned/lib -ljpeg -lwebpdemux -lwebp'
            def run(command, **kwargs):
                calls.append((command, kwargs['env'].copy(), kwargs.get('cwd')))
                if command[:2] == ['go', 'build']:
                    raise BuildBoundaryReached()
                return SimpleNamespace(returncode=0)
            with self.subTest(forwarded=forwarded), \
                 patch.dict(os.environ, self.adversarial_build_environment(), clear=True), \
                 patch.dict(sys.modules, {'django.conf': SimpleNamespace(settings=None)}), \
                 patch('subprocess.check_output', side_effect=pkg_config), \
                 patch('subprocess.run', side_effect=run), \
                 patch('socket.socket', side_effect=AssertionError('socket forbidden')):
                environment = reuse.sanitized_tool_environment() if forwarded == 'sanitized' else dict(os.environ)
                with self.assertRaises(BuildBoundaryReached):
                    with runtime.go_service(reuse, {'service_cleanup': {}}, Path('/private/tmp/owned-build'),
                                            io.StringIO(), environment, 1, False):
                        self.fail('must stop before sockets or service startup')
            self.assertEqual([command[0] for command, _, _ in calls], ['pkg-config', 'cc', 'go'])
            for command, environment, cwd in calls:
                self.assert_owned_build_environment(environment)
                self.assertNotIn('/outside/', ' '.join(command))
            self.assertEqual(calls[-1][0], ['go', 'build', '-o',
                '/private/tmp/owned-build/payment-categories-server', './cmd/server'])
            self.assertEqual(calls[-1][2], adapter.ROOT / 'BE_GO')

    def test_main_owns_build_environment_before_private_execution_and_restores_ambient(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        production_runtime = importlib.import_module('TESTS.adapters.django.production_runtime')
        applied = []
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, self.adversarial_build_environment(), clear=True):
            before = dict(os.environ)
            with patch.object(production_runtime, 'require_production_python', return_value={'python': '3.13.16'}), \
                 patch.object(reuse, 'service_binary_paths', return_value={}), \
                 patch.object(runtime, 'execute_private', side_effect=lambda *args: applied.append(dict(os.environ))) as execute, \
                 patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(runtime.main(['--output', str(Path(directory) / 'fresh')]), 1)
            self.assertEqual(dict(os.environ), before)
        execute.assert_called_once()
        self.assert_owned_build_environment(execute.call_args.args[-1])
        self.assertEqual(applied, [execute.call_args.args[-1]])

    def test_preflight_mock_targets_imported_module_despite_stale_package_attribute(self):
        package = importlib.import_module('TESTS.adapters.django')
        importlib.import_module('TESTS.adapters.django.production_runtime')
        stale = SimpleNamespace(require_production_python=lambda: self.fail('stale module used'))
        with patch.object(package, 'production_runtime', stale):
            self.test_main_owns_build_environment_before_private_execution_and_restores_ambient()

    def test_integrated_workloads_share_contexts_and_cleanup_after_success_or_lifecycle_failure(self):
        for failure in (None, 'django-native', 'go-native', 'caddy-v1', 'caddy-v2', 'gateway-setup', 'guard-missing', 'gateway-between-labels'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                events, inspected = [], []
                raw = SimpleNamespace(ping=lambda: True, close=lambda: events.append('redis-client'))
                override = SimpleNamespace(enable=lambda: events.append('override-on'),
                    disable=lambda: events.append('override-off'))
                connections = SimpleNamespace(close_all=lambda: events.append('db-close'))
                modules = {'django.db': SimpleNamespace(connections=connections),
                    'django.test.utils': SimpleNamespace(override_settings=lambda **kw: override),
                    'redis': SimpleNamespace(Redis=lambda **kw: raw)}
                snapshot, _ = cache_fixture()
                fixture, apps, observer = {'baseline_cache': snapshot, 'tokens': {}, 'sessions': {},
                                          'runtime': {'cases': [{}]}}, object(), object()
                fixture['baseline_state'] = {'original': []}
                backend_processes = [SimpleNamespace(pid=123, returncode=None), SimpleNamespace(pid=124, returncode=None)]
                for process in backend_processes:
                    process.poll = lambda process=process: process.returncode
                django_guard, go_guard = [runtime.go_liveness_guard(p) for p in backend_processes]
                guards = []
                @contextmanager
                def owned(*args):
                    try:
                        with (work / 'private-log').open('w') as log:
                            yield work, 1, log
                    finally:
                        events.append('database-stop')
                @contextmanager
                def django(*args, liveness_observer):
                    if failure != 'guard-missing':
                        liveness_observer(django_guard)
                    events.append('django-start')
                    try:
                        yield 'offline-django', observer, 1234
                    finally:
                        backend_processes[0].returncode = 0
                        events.append('django-stop')
                @contextmanager
                def golang(*args):
                    events.append('go-start')
                    try:
                        yield {'base_url': 'offline-go', 'proxy_upstream': 'offline-go-upstream',
                               runtime.LIFECYCLE_GUARD: go_guard}
                    finally:
                        backend_processes[1].returncode = 0
                        events.append('go-stop')
                @contextmanager
                def caddy(*args, service='caddy'):
                    events.append(service + '-start')
                    try:
                        yield 'offline-gateway', 'image', {'image_id': 'sha256:' + 'f' * 64, 'container_id': 'a' * 64}
                    finally:
                        events.append(service + '-stop')
                gateway_inspection = LifecycleGuardControls().container()
                def inspect(identifier):
                    inspected.append(identifier)
                    if failure == 'gateway-setup':
                        raise RuntimeError('private-inspect-error')
                    return copy.deepcopy(gateway_inspection)
                def scopes_workload(label, backend, base, value, actual_apps, actual_raw, actual_observer, *, workloads):
                    self.assertIs(value, fixture)
                    self.assertIs(actual_apps, apps)
                    self.assertIs(actual_raw, raw)
                    self.assertIs(actual_observer, observer)
                    events.append('scopes:' + label)
                    workloads.append({'label': label})
                def lifecycle_workload(label, backend, base, value, actual_apps, actual_raw, actual_observer):
                    self.assertIs(value['baseline_cache'], snapshot)
                    self.assertIs(value['baseline_state'], fixture['baseline_state'])
                    self.assertIs(actual_apps, apps)
                    self.assertIs(actual_raw, raw)
                    self.assertIs(actual_observer, observer)
                    self.assertEqual(events[-1], 'scopes:' + label)
                    events.append('lifecycle:' + label)
                    guard = value[runtime.LIFECYCLE_GUARD]
                    restarted = failure == 'gateway-between-labels' and label == 'caddy-v2'
                    if failure != 'guard-missing':
                        self.assertEqual(guard()['running'], not restarted)
                        guards.append(guard)
                    receipt = synthetic_lifecycle_receipt(label)
                    if label == failure or failure == 'guard-missing' or restarted:
                        receipt['success'] = False
                    if failure == 'gateway-between-labels' and label == 'caddy-v1':
                        gateway_inspection['RestartCount'] += 1
                    return receipt
                reuse = SimpleNamespace(owned_message_database=owned,
                    private_environment=lambda *args: ({}, set()), apply_private_environment=lambda env: None,
                    initialize_django_apps=lambda: (apps, None), cache_override=lambda port: {},
                    migrate_full_schema=lambda log: {'applied_migrations': 548},
                    django_application=django, owned_caddy=caddy)
                report = {'workloads': [], 'lifecycle_workloads': []}
                with patch.dict(sys.modules, modules), patch.object(runtime, 'go_service', new=golang), \
                     patch.object(adapter, 'snapshot_state', return_value={'original': []}), \
                     patch.object(cache, 'snapshot_historical_read_cache', return_value=snapshot), \
                     patch.object(adapter, 'seed_fixture', return_value=fixture), \
                     patch.object(adapter.shared, 'delete_created', return_value=True), \
                     patch.object(adapter, 'restore_cache', side_effect=lambda *a: events.append('cache-restored')), \
                     patch.object(adapter, 'run_workload', side_effect=scopes_workload), \
                     patch.object(lifecycle, 'run_workload', side_effect=lifecycle_workload), \
                     patch.object(runtime, 'inspect_owned_caddy', side_effect=inspect), \
                     patch.object(runtime, 'misroute_control', return_value={}), \
                     patch('socket.socket', side_effect=AssertionError('socket forbidden')):
                    if failure is None:
                        runtime.execute_private(reuse, SimpleNamespace(versioned=True), report, work, set(), {})
                        self.assertTrue(report['runtime_executed'])
                        self.assertEqual([v['label'] for v in report['lifecycle_workloads']], list(runtime.expected_labels(True)))
                    else:
                        code = 'lifecycle.gateway-guard' if failure == 'gateway-setup' else 'lifecycle.workload-failed'
                        with self.assertRaisesRegex(AssertionError, '^' + code + '$'):
                            runtime.execute_private(reuse, SimpleNamespace(versioned=True), report, work, set(), {})
                        self.assertNotIn('runtime_executed', report)
                self.assertNotIn(runtime.LIFECYCLE_GUARD, fixture)
                self.assertTrue(report['fixture_cleanup']['verified'])
                self.assertTrue(report['resource_cleanup']['verified'])
                self.assertIn('cache-restored', events)
                self.assertIn('django-stop', events)
                if 'go-start' in events: self.assertIn('go-stop', events)
                if 'caddy-start' in events: self.assertIn('caddy-stop', events)
                self.assertEqual(events[-4:], ['redis-client', 'db-close', 'override-off', 'database-stop'])
                for guard in guards:
                    self.assertFalse(guard()['running'])
                if inspected: self.assertTrue(all(identifier == 'a' * 64 for identifier in inspected))

    def test_public_main_keeps_lifecycle_receipts_on_failure_and_redacts_private_error(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        production = importlib.import_module('TESTS.adapters.django.production_runtime')
        failed = synthetic_lifecycle_receipt('django-native')
        failed['success'] = False
        def execute(reuse, args, report, *rest):
            report['lifecycle_workloads'].append(failed)
            raise RuntimeError('private Docker SQL token')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'fresh'
            with patch.object(production, 'require_production_python', return_value={'python': '3.13.16'}), \
                 patch.object(reuse, 'service_binary_paths', return_value={}), \
                 patch.object(runtime, 'execute_private', side_effect=execute), \
                 patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(runtime.main(['--output', str(output)]), 1)
            published = (output / 'results.json').read_text()
            receipt = json.loads(published)
        self.assertEqual(receipt['lifecycle_workloads'], [failed])
        self.assertEqual(receipt['errors'], [{'check': 'unknown'}])
        self.assertFalse(receipt['complete_message_family_parity'])
        self.assertNotIn('private Docker', published)

    def test_source_binding_covers_owned_and_reused_inputs(self):
        hashes = runtime.source_hashes()
        for name in runtime.OWNED_SOURCES + ('TESTS/adapters/django/impersonation_effects.py',
                     'BE/core/authentication.py', 'BE/application/impersonation.py',
                     'BE_GO/internal/service/identity.go', 'BE_GO/internal/service/message_reads.go'):
            self.assertIn(name, hashes)
        changed = dict(hashes)
        changed[runtime.OWNED_SOURCES[0]] = '0' * 64
        self.assertFalse(runtime.binding_valid(hashes, changed, hashes))
        self.assertFalse(runtime.binding_valid(hashes, hashes, {}))
        self.assertTrue(runtime.binding_valid(hashes, hashes, hashes))

    def test_optimized_interpreter_fails_before_any_runtime_action(self):
        result = subprocess.run([sys.executable, '-O', '-c',
            'from TESTS.adapters.django.message_read_scopes_runtime import require_assertions; require_assertions()'],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('optimized-python', result.stderr)

    def test_cleanup_attempts_every_step_after_partial_failure(self):
        called = []
        def broken():
            called.append('first')
            raise RuntimeError('private-value-must-not-escape')
        receipt = adapter.cleanup_steps((('one', broken), ('two', lambda: called.append('second'))))
        self.assertEqual(called, ['first', 'second'])
        self.assertEqual(receipt['results'], {'one': False, 'two': True})
        self.assertNotIn('private-value', json.dumps(receipt))
        self.assertFalse(receipt['verified'])

    def test_misrouting_rejected_by_actual_asgi_observation(self):
        snapshot, contrasts = cache_fixture()
        observed = [{'seq': 0, 'method': 'GET', 'path': '/communications/messages/list',
                     'cid': 'control', 'status': 200, 'error_type': None}]
        with self.assertRaises(shared.AttributionError):
            adapter.verify_read(backend='go', method='GET', path='/communications/messages/list',
                status=200, observed=observed, cid='control',
                before_state={'model': []}, after_state={'model': []},
                before_cache=snapshot, after_cache=snapshot, contrasts=contrasts)

    def test_state_and_cache_verified_even_after_response_failure(self):
        events = []
        class Observer:
            def cursor(self): return 0
            def delta(self, cursor): return []
        case = adapter.runtime_manifest(contract.load_source(),
            {case['actor']: 'secret' for case in contract.load_source()['cases']},
            {name: 'b1000000-0000-4000-8000-' + f'{index:012d}'
             for index, name in enumerate(adapter.SESSION_SPECS, 1)})['cases'][0]
        def request(*args): raise ValueError('private response')
        with patch.object(adapter, 'verify_read', side_effect=lambda **kw: events.append(kw)), \
             self.assertRaises(ValueError):
            adapter.observed_case(case, label='go-native', backend='go', base_url='http://offline.invalid',
                observer=Observer(), snapshot_state=lambda: {'model': []}, snapshot_cache=lambda: 'cache',
                contrasts=None, request=request)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['before_state'], events[0]['after_state'])

    def test_no_read_effect_exclusion_including_audits(self):
        for label in ('auditlog.LogEntry', 'application.AuditLogIndex', 'application.User'):
            with self.subTest(label=label), self.assertRaises(shared.AcceptanceStateError):
                adapter.verify_read(backend='go', method='GET', path='/communications/messages/list', status=200,
                    observed=[], cid='control', before_state={label: []}, after_state={label: [{'id': 1}]},
                    before_cache='cache', after_cache='cache', contrasts=None)

    def test_snapshot_requires_all_128_models(self):
        with patch.object(shared, 'snapshot_models', return_value={'one': []}):
            with self.assertRaisesRegex(AssertionError, '128'):
                adapter.snapshot_state(None)

    def test_cache_session_type_dump_expiry_and_key_mutations_are_not_excluded(self):
        before, contrasts = cache_fixture()
        key = b':1:impersonation:v1:private'
        for mutation in ('type', 'dump', 'expiry', 'removed', 'new'):
            entries = dict(before.entries)
            if mutation == 'type': entries[key] = cache.HistoricalReadCacheEntry(b'hash', b'session-dump', 4000000000000)
            if mutation == 'dump': entries[key] = cache.HistoricalReadCacheEntry(b'string', b'changed', 4000000000000)
            if mutation == 'expiry': entries[key] = cache.HistoricalReadCacheEntry(b'string', b'session-dump', 4000000000001)
            if mutation == 'removed': entries.pop(key)
            if mutation == 'new': entries[b'unexpected'] = entries[key]
            after = cache.HistoricalReadCacheSnapshot(MappingProxyType(entries))
            with self.subTest(mutation=mutation), self.assertRaises(shared.AcceptanceStateError):
                adapter.verify_read(backend='go', method='GET', path='/communications/messages/list',
                    status=200, observed=[], cid='control', before_state={'model': []},
                    after_state={'model': []}, before_cache=before, after_cache=after, contrasts=contrasts)

    def test_restore_cache_preserves_dump_and_absolute_expiration(self):
        original, _ = cache_fixture()
        current = cache.HistoricalReadCacheSnapshot(MappingProxyType({
            b'unexpected-session': cache.HistoricalReadCacheEntry(b'string', b'new', -1)}))
        raw = Mock()
        with patch.object(cache, 'snapshot_historical_read_cache', side_effect=(current, original)):
            self.assertTrue(adapter.restore_cache(raw, original))
        raw.delete.assert_called_once_with(b'unexpected-session')
        raw.restore.assert_any_call(cache.PERSISTENT_CONTRAST_KEY, 0, b'persistent-dump', replace=True)
        raw.restore.assert_any_call(cache.EXPIRING_CONTRAST_KEY, 4000000000000,
                                    b'expiring-dump', replace=True, absttl=True)
        with patch.object(cache, 'snapshot_historical_read_cache', side_effect=(current, current)):
            with self.assertRaisesRegex(AssertionError, '^cleanup.cache-restored$'):
                adapter.restore_cache(raw, original)

    def test_partial_seed_failure_restores_cache_private_files_and_resources(self):
        events = []
        raw = SimpleNamespace(ping=lambda: True, close=lambda: events.append('redis-client'))
        override = SimpleNamespace(enable=lambda: events.append('override-on'),
                                   disable=lambda: events.append('override-off'))
        connections = SimpleNamespace(close_all=lambda: events.append('db-close'))
        # Fake backend interfaces, without starting/importing any actual service.
        modules = {'django.db': SimpleNamespace(connections=connections),
                   'django.test.utils': SimpleNamespace(override_settings=lambda **kw: override),
                   'redis': SimpleNamespace(Redis=lambda **kw: raw)}
        snapshot, _ = cache_fixture()
        report = {}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            private = work / 'message-reads-private'

            @contextmanager
            def owned(*args):
                with (work / 'private-log').open('w') as log:
                    yield work, 1, log

            reuse = SimpleNamespace(owned_message_database=owned,
                private_environment=lambda *args: ({}, set()), apply_private_environment=lambda env: None,
                initialize_django_apps=lambda: (None, None), cache_override=lambda port: {},
                migrate_full_schema=lambda log: {'applied_migrations': 548})

            def broken_seed(*args):
                private.mkdir()
                (private / 'tokenized.json').write_text('private-token')
                raise RuntimeError('private-seed-error')

            with patch.dict(sys.modules, modules), patch.object(adapter, 'snapshot_state', return_value={'original': []}), \
                 patch.object(cache, 'snapshot_historical_read_cache', return_value=snapshot), \
                 patch.object(adapter, 'seed_fixture', side_effect=broken_seed), \
                 patch.object(adapter, 'restore_cache', side_effect=lambda *args: events.append('cache-restored')):
                with self.assertRaisesRegex(RuntimeError, 'private-seed-error'):
                    runtime.execute_private(reuse, SimpleNamespace(versioned=False), report, work, set(), {})
            self.assertFalse(private.exists())
        self.assertTrue(report['fixture_cleanup']['verified'])
        self.assertTrue(report['resource_cleanup']['verified'])
        self.assertIn('cache-restored', events)
        self.assertEqual(events[-3:], ['redis-client', 'db-close', 'override-off'])
        self.assertNotIn('private-token', json.dumps(report))

    def test_each_observer_checked_after_state_failure(self):
        snapshot, contrasts = cache_fixture()
        with patch.object(shared, 'assert_state_unchanged', side_effect=shared.AcceptanceStateError('state')), \
             patch.object(shared, 'assert_cache_unchanged') as cache_check, \
             patch.object(shared, 'assert_attribution') as attribution:
            with self.assertRaises(shared.AcceptanceStateError):
                adapter.verify_read(backend='go', method='GET', path='/communications/messages/list', status=200,
                    observed=[], cid='control', before_state={}, after_state={}, before_cache=snapshot,
                    after_cache=snapshot, contrasts=contrasts)
        cache_check.assert_called_once()
        attribution.assert_called_once()

    def test_optimized_main_rejects_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'must-not-exist'
            result = subprocess.run([sys.executable, '-O', '-m',
                'TESTS.adapters.django.message_read_scopes_runtime', '--output', str(destination)],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('optimized-python', result.stderr)
            self.assertFalse(destination.exists())

    def test_diagnostics_allow_only_known_check_codes_and_bounded_integer_status(self):
        for code in contract.HTTP_CHECKS:
            self.assertEqual(adapter.failure_diagnostic(contract.ResponseFailure(code, 503)),
                             {'check': code, 'status': 503})
        for error_type, code in ((adapter.StateFailure, 'state'), (adapter.CacheFailure, 'cache'),
                                 (shared.AttributionError, 'attribution'), (RuntimeError, 'unknown'),
                                 (AssertionError, 'unknown')):
            error = error_type('private-token body SQL headers password')
            error.actual_status = 403
            self.assertEqual(adapter.failure_diagnostic(error), {'check': code, 'status': 403})
        for status in ('private-status', True, -1, 600, None):
            diagnostic = adapter.failure_diagnostic(contract.ResponseFailure('private-code', status))
            self.assertEqual(diagnostic, {'check': 'unknown'})

    def test_failed_workload_keeps_safe_assertion_case_and_status(self):
        source = contract.load_source()
        converted = adapter.runtime_manifest(source,
            {case['actor']: 'private-token' for case in source['cases']},
            {name: 'b1000000-0000-4000-8000-' + f'{index:012d}'
             for index, name in enumerate(adapter.SESSION_SPECS, 1)})
        for error, expected in ((contract.ResponseFailure('response.body', 200),
                                 {'check': 'response.body', 'status': 200}),
                                (AssertionError('response.body private-token SQL'), {'check': 'unknown'})):
            workloads = []
            with patch.object(shared, 'assert_state_unchanged'), patch.object(shared, 'assert_cache_unchanged'), \
                 patch.object(adapter, 'snapshot_state', return_value={}), \
                 patch.object(cache, 'snapshot_historical_read_cache'), \
                 patch.object(adapter, 'observed_case', side_effect=error):
                with self.assertRaises(AssertionError):
                    adapter.run_workload('django-native', 'django', 'http://offline.invalid',
                        {'runtime': converted, 'baseline_state': {}, 'baseline_cache': None, 'contrasts': None},
                        None, None, None, workloads=workloads)
            self.assertEqual(workloads, [{'label': 'django-native', 'success': False,
                'cases': [{'case': source['cases'][0]['name'], **expected}]}])
            self.assertNotIn('private-token', json.dumps(workloads))

    def test_main_unknown_private_exception_is_not_published(self):
        production_runtime = importlib.import_module('TESTS.adapters.django.production_runtime')
        environment = dict(os.environ)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'fresh'
            with patch.object(production_runtime, 'require_production_python',
                              side_effect=RuntimeError('private-token raw-SQL body-header')):
                with patch('sys.stdout', new=io.StringIO()):
                    self.assertEqual(runtime.main(['--output', str(output)]), 1)
            published = (output / 'results.json').read_text()
            report = json.loads(published)
        self.assertEqual(report['errors'], [{'check': 'unknown'}])
        self.assertFalse(report['success'])
        self.assertNotIn('private-token', published)
        self.assertNotIn('raw-SQL', published)
        self.assertEqual(dict(os.environ), environment)

    def candidate(self, versioned=False):
        cases = contract.load_source()['cases']
        hashes = runtime.source_hashes()
        report = {'runtime_executed': True, 'errors': [], 'cleanup_errors': [], 'versioned': versioned,
                  'source_sha256': hashes, 'source_sha256_after': hashes.copy(),
                  'go_build_source_sha256': hashes.copy(), 'schema': {'applied_migrations': 548,
                  'remaining_plan': 0, 'mode': 'full-migration-history'}, 'cleanup_verified': True,
                  'fixture_cleanup': {'verified': True, 'results': dict.fromkeys(runtime.FIXTURE_CLEANUP, True)},
                  'go_artifact': {'server_sha256': 'a' * 64, 'codec_sha256': 'b' * 64, 'process_exit': 0},
                  'owned_directory_removed': True, 'private_directory_removed': True,
                  'environment_restored': True,
                  'resource_cleanup': {'verified': True, 'results': dict.fromkeys(
                      ('redis_client', 'db_connections', 'cache_override'), True)},
                  'sensitive_cleanup': {'verified': True, 'errors': [], 'results': dict.fromkeys(
                      ('tokenized_fixture_removed', 'private_sql_log_removed', 'private_query_observation_removed'), True)},
                  'python': {'python': '3.13.16', 'sha256': 'e' * 64},
                  'service_binary_sha256': dict.fromkeys(('postgres', 'initdb', 'pg_ctl', 'createdb',
                      'psql', 'redis-server', 'go') + (('docker',) if versioned else ()), 'f' * 64),
                  'service_cleanup': dict.fromkeys(('django', 'go', 'postgres', 'redis') +
                       (('caddy', 'caddy-misroute') if versioned else ()), 'stopped'),
                  'workloads': [], 'lifecycle_workloads': [], 'complete_message_family_parity': False}
        for label in runtime.expected_labels(versioned):
            report['lifecycle_workloads'].append(synthetic_lifecycle_receipt(label))
            baseline = report['lifecycle_workloads'][-1]['baseline']
            report['workloads'].append({'label': label, 'success': True, 'cases': [
                {'case': case['name'], 'status': case['status'], 'check': case['check'],
                 'checks': dict.fromkeys(('response', 'state', 'cache', 'attribution'), True),
                 'models': 128, 'state_sha256': baseline['state_sha256'],
                 'cache_sha256': baseline['cache_sha256']}
                for case in cases]})
        if versioned:
            report['caddy_image_sha256'] = 'f' * 64
            report['misroute_control'] = {'rejected': True, 'actual_django_events': 1,
                                         'state': True, 'cache': True, 'status': 200}
        return report

    def test_only_complete_bound_cleaned_receipt_passes(self):
        for versioned in (False, True):
            report = self.candidate(versioned)
            self.assertTrue(runtime.acceptance(report))
            for change in ('case', 'status', 'check', 'models', 'cache', 'artifact', 'exit', 'cleanup',
                           'source', 'error', 'schema', 'order', 'fixture', 'empty'):
                changed = copy.deepcopy(report)
                if change == 'case': changed['workloads'][0]['cases'].pop()
                if change == 'status': changed['workloads'][0]['cases'][0]['status'] = 500
                if change == 'check': changed['workloads'][0]['cases'][0]['check'] = 'other'
                if change == 'models': changed['workloads'][0]['cases'][0]['models'] = 127
                if change == 'cache': changed['workloads'][0]['cases'][0]['checks']['cache'] = False
                if change == 'artifact': changed['go_artifact'].pop('codec_sha256')
                if change == 'exit': changed['go_artifact']['process_exit'] = 7
                if change == 'cleanup': changed['cleanup_verified'] = False
                if change == 'source': changed['source_sha256_after'] = {}
                if change == 'error': changed['errors'] = ['failed']
                if change == 'schema': changed['schema']['applied_migrations'] = 1
                if change == 'order': changed['workloads'].reverse()
                if change == 'fixture': changed['fixture_cleanup']['results'].pop('cache_restored')
                if change == 'empty': changed = {}
                with self.subTest(versioned=versioned, change=change):
                    self.assertFalse(runtime.acceptance(changed))

    def test_lifecycle_receipts_require_exact_labels_order_cases_and_acceptance(self):
        report = self.candidate(True)
        self.assertTrue(runtime.acceptance(report))
        for change in ('missing-field', 'missing-label', 'order', 'duplicate', 'missing-phase',
                       'reordered-scenario', 'failed', 'missing-guard', 'gateway-restart', 'extra-private'):
            changed = copy.deepcopy(report)
            receipt = changed['lifecycle_workloads'][0]
            if change == 'missing-field': changed.pop('lifecycle_workloads')
            if change == 'missing-label': changed['lifecycle_workloads'].pop()
            if change == 'order': changed['lifecycle_workloads'].reverse()
            if change == 'duplicate': changed['lifecycle_workloads'][-1] = receipt
            if change == 'missing-phase': receipt['scenarios'][0]['phases'].pop()
            if change == 'reordered-scenario': receipt['scenarios'].reverse()
            if change == 'failed': receipt['success'] = False
            if change == 'missing-guard': receipt['process_sha256'] = None
            if change == 'gateway-restart': changed['lifecycle_workloads'][2]['scenarios'][0]['phases'][1]['checks']['process'] = False
            if change == 'extra-private': receipt['private'] = 'private'
            with self.subTest(change=change):
                self.assertFalse(runtime.acceptance(changed))

    def test_versioned_needs_actual_rejected_misroute(self):
        report = self.candidate(True)
        for change in ('missing', 'accepted', 'no-events', 'state'):
            changed = copy.deepcopy(report)
            if change == 'missing': changed.pop('misroute_control')
            if change == 'accepted': changed['misroute_control']['rejected'] = False
            if change == 'no-events': changed['misroute_control']['actual_django_events'] = 0
            if change == 'state': changed['misroute_control']['state'] = False
            self.assertFalse(runtime.acceptance(changed))

    def test_every_scopes_case_must_match_its_paired_lifecycle_baseline(self):
        report = self.candidate(True)
        self.assertTrue(runtime.acceptance(report))
        for label_index, label in enumerate(runtime.expected_labels(True)):
            for field in ('state_sha256', 'cache_sha256'):
                for case_index in (0, 19, 37, 'all'):
                    changed = copy.deepcopy(report)
                    cases = changed['workloads'][label_index]['cases']
                    for case in cases if case_index == 'all' else [cases[case_index]]:
                        case[field] = 'e' * 64
                    with self.subTest(label=label, field=field, case=case_index):
                        self.assertFalse(runtime.acceptance(changed))

    def test_internally_valid_lifecycle_baseline_cannot_disagree_with_paired_scopes(self):
        report = self.candidate(False)
        self.assertTrue(runtime.acceptance(report))
        for label_index, label in enumerate(runtime.expected_labels(False)):
            for field in ('state_sha256', 'cache_sha256'):
                changed = copy.deepcopy(report)
                receipt = changed['lifecycle_workloads'][label_index]
                receipt['baseline'][field] = 'e' * 64
                receipt['restoration'][field] = 'e' * 64
                for scenario in receipt['scenarios']:
                    scenario['restoration'][field] = 'e' * 64
                    scenario['phases'][0][field] = 'e' * 64
                    if field == 'state_sha256':
                        scenario['transition'][field] = 'e' * 64
                        scenario['phases'][1][field] = 'e' * 64
                with self.subTest(label=label, field=field):
                    self.assertTrue(lifecycle.acceptance(receipt))
                    self.assertFalse(runtime.acceptance(changed))

    def test_cleanup_python_tool_and_artifact_evidence_cannot_be_asserted_away(self):
        report = self.candidate(True)
        for mutation in ('directory', 'private-directory', 'environment', 'resources', 'sensitive',
                         'python', 'tool', 'image', 'exit-bool', 'source-subset', 'hash'):
            changed = copy.deepcopy(report)
            if mutation == 'directory': changed['owned_directory_removed'] = False
            if mutation == 'private-directory': changed['private_directory_removed'] = False
            if mutation == 'environment': changed['environment_restored'] = False
            if mutation == 'resources': changed['resource_cleanup']['results']['db_connections'] = False
            if mutation == 'sensitive': changed['sensitive_cleanup']['results'].pop('private_sql_log_removed')
            if mutation == 'python': changed['python']['python'] = '3.14.6'
            if mutation == 'tool': changed['service_binary_sha256'].pop('go')
            if mutation == 'image': changed['caddy_image_sha256'] = ''
            if mutation == 'exit-bool': changed['go_artifact']['process_exit'] = False
            if mutation == 'source-subset':
                for key in ('source_sha256', 'source_sha256_after', 'go_build_source_sha256'):
                    changed[key] = {runtime.OWNED_SOURCES[0]: report[key][runtime.OWNED_SOURCES[0]]}
            if mutation == 'hash': changed['workloads'][0]['cases'][0]['cache_sha256'] = ''
            with self.subTest(mutation=mutation):
                self.assertFalse(runtime.acceptance(changed))

    def test_modules_import_without_environment_changes_or_backend_imports(self):
        code = '''
import os, sys
before = dict(os.environ)
from TESTS.adapters.django import message_read_scopes, message_read_scopes_runtime
assert dict(os.environ) == before
assert not any(name.startswith(('django', 'application', 'uvicorn', 'redis')) for name in sys.modules)
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class FixtureExpiryClockControls(unittest.TestCase):
    def test_invalid_or_unavailable_server_clock_never_mutates_session(self):
        for clock in [(-1, 0), (1000, -1), (True, 0), (1000, 1_000_000)]:
            with self.subTest(clock=clock):
                raw = SimpleNamespace(time=Mock(return_value=clock), pexpireat=Mock(), delete=Mock())
                with self.assertRaises(AssertionError):
                    adapter.expire_fixture_session(raw, b'owned-session')
                raw.pexpireat.assert_not_called()
                raw.delete.assert_not_called()
        raw.time.side_effect = RuntimeError('clock unavailable')
        with self.assertRaises(RuntimeError):
            adapter.expire_fixture_session(raw, b'owned-session')
        raw.pexpireat.assert_not_called()
        raw.delete.assert_not_called()

    def test_expiry_uses_server_clock_with_ahead_or_behind_host(self):
        class RedisClock:
            def __init__(self):
                self.live = True
                self.calls = []
            def time(self):
                return (1000, 123456)
            def pexpireat(self, key, deadline):
                self.calls.append((key, deadline))
                existed = self.live
                if deadline <= 1000123:
                    self.live = False
                return existed
        for host_time in (1060, 940):
            with self.subTest(host_time=host_time), patch.object(adapter.time, 'time', return_value=host_time):
                raw = RedisClock()
                self.assertIs(adapter.expire_fixture_session(raw, b'owned-session'), True)
                self.assertFalse(raw.live)
                self.assertEqual(raw.calls, [(b'owned-session', 1000122)])


if __name__ == '__main__':
    unittest.main()
