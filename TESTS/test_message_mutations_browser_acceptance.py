"""Offline controls for the maintained composition; these are not live evidence."""
import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from TESTS.adapters.django import message_mutations_browser_acceptance as cli


class OfflineControls(unittest.TestCase):
    def test_inert_import_and_shared_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = []
            for name in cli.PATH_OPTIONS:
                path = root / name
                path.touch()
                flags += ['--' + name.replace('_', '-'), str(path)]
            args = cli.parse_args(flags + ['--output', str(root / 'fresh'),
                                           '--deadline', '1500', '--cleanup-grace', '180'])
            self.assertEqual((args.timeout, args.deadline, args.cleanup_grace), (360, 1500, 180))
            self.assertFalse(args.output.exists())
            with self.assertRaises(cli.CliError):
                cli.parse_args(flags + ['--output', str(root / 'fresh'), '--timeout', '29'])

    def test_database_bounds_precede_app_ready(self):
        events = []
        settings = SimpleNamespace(DATABASES={'default': {'OPTIONS': {'options': '-c timezone=UTC'}}})
        def setup():
            options = settings.DATABASES['default']['OPTIONS']
            self.assertEqual(options['connect_timeout'], 5)
            self.assertIn('statement_timeout=30000', options['options'])
            self.assertIn('lock_timeout=5000', options['options'])
            events.append('ready')
            return 'apps', settings
        with patch.object(cli.s.runtime, 'initialize_django_apps', setup):
            self.assertEqual(cli.initialize_bounded_django(settings), ('apps', settings))
        self.assertEqual(events, ['ready'])

    def test_source_graph_includes_entry_test_docs_and_helpers(self):
        graph = cli.source_hashes()
        for name in (*cli.OWNED, 'TESTS/adapters/go/message_image_execution.py',
                     'TESTS/adapters/django/message_mutations.py',
                     'TESTS/adapters/django/message_mutations_browser_denials.py'):
            self.assertIn(name, graph)
        self.assertEqual(graph[cli.OWNED[0]], cli.s.sha(Path(cli.__file__)))


DIGEST = 'a' * 64


def frozen_inputs():
    sources = {name: DIGEST for name in cli.OWNED}
    sources.update({name: DIGEST for name in cli.denials.source_hashes()})
    return {'python': {'version': '3.13.16'}, 'sources': sources,
            'provenance': {'artifact_sha256': {'index.html': DIGEST}, 'source_sha256': {'UI/index.html': DIGEST}},
            'build': {'artifact_sha256': {'index.html': DIGEST}, 'source_sha256': {'UI/index.html': DIGEST},
                      'invocation': ['vite', 'build'], 'record_sha256': DIGEST},
            'native': {'artifacts': {'server': DIGEST, 'image-codec': DIGEST},
                       'source_sha256': {'BE_GO/main.go': DIGEST}, 'record_sha256': DIGEST},
            'modules': {'version': '1.50.0', 'files': {'playwright/index.js': DIGEST}},
            'binaries': {'postgres': {'path': '/offline/bin/postgres', 'sha256': DIGEST}},
            'paths': {name: '/offline/' + name for name in (*cli.PATH_OPTIONS, 'node')},
            'options': {'timeout': 360, 'deadline': 1500, 'cleanup_grace': 180}}


def browser_row(version, frozen):
    events = []
    for actor, action in cli.browser.CHECKPOINTS:
        spec = cli.browser.authored_spec()['actors'][actor]
        plan = ([('POST', cli.browser.LOGIN, 200)] if action == 'login' else [] if action == 'closed'
                else ([(spec[action]['method'], spec[action]['path'], spec[action]['status'])]
                      if action in ('create', 'delete') else []) +
                     [('GET', cli.browser.LIST, 403 if actor == 'restricted_collaborator' else 200)])
        for method, path, status in plan:
            backend = 'django-v1' if version == 'v1' else 'go-v2'
            events.append(dict(cid=f'message-mutations-{version}-{actor}-{action}-{len(events)+1}',
                method=method, path=path, status=status, backend=backend, intended_backend=backend,
                fallback=False, peer=['127.0.0.1', 1234], intended_peer=['127.0.0.1', 1234]))
    return dict(version=version, acceptance=dict(success=True, version=version,
        checkpoints_verified=24, selected_requests_verified=26,
        complete_message_family_parity=False, http_denials_verified=False),
        proxy_cleanup_verified=True, browser_child_reaped=True, real_cookie_forwarding=True,
        http_denials_verified=False, complete_message_family_parity=False,
        fixture_cleanup_owner='parent', proxy_observations=events,
        context_close_proofs=[{'actor': actor, 'observed_after_context_close': True}
                              for actor in cli.browser.ACTORS],
        final_state_proof={'models': {'models': 128, 'rows': 679, 'sha256': DIGEST},
                          'cache': dict(typed_values_exact=True, absolute_expirations_exact=True,
                            contrast_keys_verified=2, readiness_prefix_excluded=False,
                            unexpected_readiness_keys=0, cache_sha256=DIGEST)},
        transport_probe=dict(cookies_preserved=True, post_body_preserved=True, redirect_blocked=True),
        browser_binary_sha256=DIGEST, playwright_files_sha256=cli.s.json_digest(frozen['modules']['files']),
        playwright_version=frozen['modules']['version'], native_build=copy.deepcopy(frozen['native']),
        provenance_before=copy.deepcopy(frozen['provenance']), provenance_after=copy.deepcopy(frozen['provenance']))


def denial_row(version, frozen):
    return dict(success=True, version=version, http_denials_verified=True,
        proxy_cleanup_verified=True, models=128, browser_acceptance=False,
        complete_message_family_parity=False, fixture_cleanup_owner='parent',
        source_sha256={name: frozen['sources'][name] for name in cli.denials.source_hashes()},
        cases=[dict(verified=True, case=case['name'], status=case['status'], source='parent-http',
                    **dict.fromkeys(('response_sha256', 'wire_sha256', 'state_sha256', 'cache_sha256'), DIGEST))
               for case in cli.browser.authored_spec()['denials']])


def fixture_receipt(version):
    return dict(version=version, verified=True, owned_logo_removed=True,
        results=dict.fromkeys(('rows', 'cache', 'sequences', 'private_fixture'), True),
        login_restore=dict(all_128_models_restored=True, complete_db1_restored=True,
            cache_sha256=DIGEST, models=dict(models=128, rows=679, sha256=DIGEST)))


def complete_report():
    frozen = frozen_inputs()
    report = cli.new_report()
    report.update(success=True, browser_acceptance=True, http_denials_verified=True,
        cleanup_verified=True, inputs_stable=True, private_directory_removed=True,
        environment_restored=True, umask_restored=True, audit_resolvers_after_migration=True,
        service_cleanup=dict.fromkeys(cli.SERVICES, 'stopped'), owned_directory_removed=True,
        postgres_stopped=True, redis_stopped=True, source_sha256_before=frozen['sources'],
        source_sha256_after=copy.deepcopy(frozen['sources']), parent_driver_sha256=DIGEST,
        input_binding=frozen, native_build=frozen['native'],
        sensitive_cleanup=dict(verified=True, errors=[], results=dict.fromkeys(
            ('tokenized_fixture_removed', 'private_sql_log_removed', 'private_query_observation_removed'), True)),
        schema=dict(mode='full-migration-history', remaining_plan=0, applied_migrations=547),
        private_io_bounds=dict(connect_seconds=5, redis_socket_seconds=5, statement_seconds=30,
            lock_seconds=5, execution_seconds=1500, cleanup_grace_seconds=180),
        fixtures=[fixture_receipt(version) for version in ('v1', 'v2')],
        versions=[browser_row(version, frozen) for version in ('v1', 'v2')],
        denial_versions=[denial_row(version, frozen) for version in ('v1', 'v2')])
    return report


class ReceiptControls(unittest.TestCase):
    def test_module_binding_is_canonical_and_rejects_old_order_and_changed_files(self):
        report = complete_report()
        files = {'test/index.js': DIGEST, 'playwright/index.js': 'b' * 64,
                 'playwright-core/index.js': 'c' * 64}
        report['input_binding']['modules']['files'] = files
        canonical = cli.s.json_digest(dict(sorted(files.items())))
        legacy = cli.s.json_digest(files)
        self.assertNotEqual(canonical, legacy)
        for row in report['versions']:
            row['playwright_files_sha256'] = canonical
        self.assertTrue(cli.validate_report(report))
        for mutation in ('legacy', 'hash', 'missing', 'extra'):
            changed = copy.deepcopy(report)
            if mutation == 'legacy':
                for row in changed['versions']:
                    row['playwright_files_sha256'] = legacy
            elif mutation == 'hash':
                changed['input_binding']['modules']['files']['test/index.js'] = 'd' * 64
            elif mutation == 'missing':
                changed['input_binding']['modules']['files'].pop('test/index.js')
            else:
                changed['input_binding']['modules']['files']['test/extra.js'] = DIGEST
            with self.subTest(mutation=mutation), self.assertRaises(cli.CliError):
                cli.validate_report(changed)

    def test_real_module_fingerprints_survive_actual_publication_and_json_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / '@playwright/test'
            for path in (module, root / 'playwright', root / 'playwright-core'):
                path.mkdir(parents=True)
                (path / 'package.json').write_text(json.dumps({'name': '@playwright/test', 'version': '1.50.0'}))
                (path / 'index.js').write_text('// offline')
            frozen = frozen_inputs()
            frozen['modules'] = cli.s.module_fingerprints(module)
            report = complete_report()
            report['input_binding'] = frozen
            report['versions'] = [browser_row(version, frozen) for version in ('v1', 'v2')]
            self.assertTrue(cli.validate_report(report))
            path = root / 'results.json'
            cli.s.browser.oracle.write_publishable(path, report, {'offline-private'})
            published = json.loads(path.read_bytes())
            self.assertTrue(cli.validate_report(published))
            for row in published['versions']:
                self.assertEqual(row['playwright_files_sha256'],
                                 cli.s.json_digest(published['input_binding']['modules']['files']))
            published['input_binding']['modules']['files']['playwright/index.js'] = 'b' * 64
            with self.assertRaises(cli.CliError):
                cli.validate_report(published)

    def test_complete_independent_summaries_pass_without_live_acceptance_claim(self):
        report = complete_report()
        self.assertTrue(cli.validate_report(json.loads(json.dumps(report))))
        self.assertFalse(report['versions'][0]['http_denials_verified'])
        self.assertFalse(report['denial_versions'][0]['browser_acceptance'])

    def test_incomplete_and_contradictory_receipts_fail(self):
        cases = {
            'failed-public-summary': lambda r: r.update(success=False),
            'failed-browser-summary': lambda r: r.update(browser_acceptance=False),
            'failed-http-summary': lambda r: r.update(http_denials_verified=False),
            'missing-version': lambda r: r['versions'].pop(),
            'duplicate-version': lambda r: r['versions'][1].update(version='v1'),
            'reordered-version': lambda r: r['denial_versions'].reverse(),
            'missing-denial': lambda r: r['denial_versions'][0]['cases'].pop(),
            'reordered-denial': lambda r: r['denial_versions'][0]['cases'].reverse(),
            'denial-response-status': lambda r: r['denial_versions'][0]['cases'][0].update(status=200),
            'denial-wire-missing': lambda r: r['denial_versions'][1]['cases'][1].pop('wire_sha256'),
            'denial-state-missing': lambda r: r['denial_versions'][1]['cases'][1].pop('state_sha256'),
            'denial-browser-shortcut': lambda r: r['denial_versions'][0].update(browser_acceptance=True),
            'browser-http-shortcut': lambda r: r['versions'][0].update(http_denials_verified=True),
            'checkpoint-coverage': lambda r: r['versions'][0]['acceptance'].update(checkpoints_verified=23),
            'response-coverage': lambda r: r['versions'][0]['acceptance'].update(selected_requests_verified=25),
            'child-unreaped': lambda r: r['versions'][0].update(browser_child_reaped=False),
            'proxy-unclosed': lambda r: r['versions'][1].update(proxy_cleanup_verified=False),
            'context-not-closed': lambda r: r['versions'][0]['context_close_proofs'][0].update(observed_after_context_close=False),
            'final-state': lambda r: r['versions'][0]['final_state_proof']['models'].update(models=127),
            'cache-expiry': lambda r: r['versions'][0]['final_state_proof']['cache'].update(absolute_expirations_exact=False),
            'fixture-rows': lambda r: r['fixtures'][0]['results'].update(rows=False),
            'fixture-full-db': lambda r: r['fixtures'][1]['login_restore'].update(all_128_models_restored=False),
            'logo-removed': lambda r: r['fixtures'][1].update(owned_logo_removed=False),
            'service-running': lambda r: r['service_cleanup'].update(go='running'),
            'private-retained': lambda r: r.update(private_directory_removed=False),
            'input-drift': lambda r: r.update(source_sha256_after={cli.OWNED[0]: 'b' * 64}),
            'entry-drift': lambda r: r.update(parent_driver_sha256='b' * 64),
            'artifact-drift': lambda r: r['versions'][0]['provenance_after']['artifact_sha256'].update(other=DIGEST),
            'build-drift': lambda r: r['versions'][0].update(native_build={}),
            'missing-frontend-build': lambda r: r['input_binding'].pop('build'),
            'stale-frontend-build': lambda r: r['input_binding']['build'].update(artifact_sha256={'other': DIGEST}),
            'invalid-binary-binding': lambda r: r['input_binding']['binaries']['postgres'].update(sha256='missing'),
            'browser-module-drift': lambda r: r['versions'][0].update(playwright_version='1.1.1'),
            'execution-cancelled': lambda r: r.update(execution={'reason': 'sigterm'}),
            'unsafe-db': lambda r: r['private_io_bounds'].update(connect_seconds=0),
            'audit-resolver': lambda r: r.update(audit_resolvers_after_migration=False),
            'sensitive-cleanup': lambda r: r['sensitive_cleanup'].update(verified=False),
            'changed-ui-coverage': lambda r: r['versions'][0]['proxy_observations'][1].update(cid='message-mutations-v1-owner-login-999'),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                report = complete_report()
                mutate(report)
                with self.assertRaisesRegex(cli.CliError, '^receipt.invalid$'):
                    cli.validate_report(report)

    def test_restricted_list_cannot_be_relabelled_200_or_404(self):
        for status in (200, 404):
            report = complete_report()
            event = next(row for row in report['versions'][0]['proxy_observations']
                         if '-restricted_collaborator-read-' in row['cid'])
            event['status'] = status
            with self.assertRaises(cli.CliError):
                cli.validate_report(report)


class CompositionControls(unittest.TestCase):
    def exercise(self, directory, fail=None):
        root = Path(directory)
        frozen = frozen_inputs()
        args = SimpleNamespace(go_binary=root / 'server', image_codec=root / 'codec')
        report = cli.new_report()
        events, secrets = [], set()
        guard = lambda: dict(pid=1234, running=True, instance='actual-owner-hook')
        @contextmanager
        def fixture(apps, path, raw, cleanup_receipt):
            events.append('fixture-' + path.name)
            try:
                yield {'tokens': {'owner': 'offline-token'}}
            finally:
                events.append('outer-reset')
                cleanup_receipt.update(verified=True)
        @contextmanager
        def django(report, liveness_observer):
            report['service_cleanup']['django'] = 'start_attempted'
            events.append('django-start')
            try:
                if fail == 'django-start':
                    raise RuntimeError('PRIVATE')
                report['service_cleanup']['django'] = 'running'
                liveness_observer(guard)
                yield 'actual-observer', 1234
            finally:
                events.append('django-stop')
                report['service_cleanup']['django'] = 'stopped'
        @contextmanager
        def go(work, binary, codec, env, log, report, native, liveness_observer):
            report['service_cleanup']['go'] = 'start_attempted'
            events.append('go-start')
            try:
                if fail == 'go-start':
                    raise RuntimeError('PRIVATE')
                report['service_cleanup']['go'] = 'running'
                liveness_observer(guard)
                yield 5678
            finally:
                events.append('go-stop')
                report['service_cleanup']['go'] = 'stopped'
        def prepare(apps, work, consumer):
            self.assertEqual(consumer, 'mutations')
            events.append('prepare')
            logo = root / 'instance/logo.png'
            logo.parent.mkdir(exist_ok=True)
            logo.write_bytes(cli.base64.b64decode(cli.s.FIXTURE_PNG))
            if fail == 'prepare':
                raise RuntimeError('PRIVATE')
            return {'owner': {'password': 'offline-password'}}
        def run_denials(**kw):
            self.assertEqual(kw['observer'], 'actual-observer')
            self.assertIs(kw['process_guard'], guard)
            self.assertEqual(kw['remote_addr'], '127.0.0.1')
            self.assertEqual(report['service_cleanup']['go'], 'running')
            events.append('denials-' + kw['version'])
            if fail == 'denials':
                raise RuntimeError('PRIVATE')
            return {'version': kw['version']}
        def run_browser(**kw):
            self.assertEqual(report['service_cleanup']['django'], 'running')
            self.assertIs(kw['process_guard'], guard)
            events.append('browser-' + kw['version'])
            if fail in ('browser-child', 'proxy-cleanup', 'browser-cleanup'):
                raise RuntimeError('PRIVATE')
            return {'version': kw['version']}
        def restore():
            self.assertTrue(all(report['service_cleanup'][name] in ('stopped', 'not_started')
                                for name in ('django', 'go')))
            events.append('inner-reset')
            return True
        with patch.object(cli.fixture_api, 'fixture_context', fixture), \
             patch.object(cli.s, 'FixtureRestore', return_value=SimpleNamespace(restore=restore)), \
             patch.object(cli.s, 'prepare_browser_identities', prepare), \
             patch.object(cli.s, 'owned_django', django), patch.object(cli.s, 'owned_go', go), \
             patch.object(cli, 'measured_address', return_value='127.0.0.1'), \
             patch.object(cli.denials, 'run_denials', run_denials), \
             patch.object(cli.mutations, 'run_version', run_browser):
            try:
                cli.execute_versions(args=args, report=report, frozen=frozen, private=root,
                    work=root, raw='raw', apps='apps', settings=SimpleNamespace(MEDIA_ROOT=root),
                    log=io.StringIO(), environment={}, tool_environment={}, secrets=secrets, publish=Mock())
            except RuntimeError:
                self.assertIsNotNone(fail)
            else:
                self.assertIsNone(fail)
        self.assertFalse((root / 'instance/logo.png').exists())
        return report, events, secrets

    def test_denials_before_browser_inside_actual_owner_hooks_per_fresh_version(self):
        with tempfile.TemporaryDirectory() as directory:
            report, events, secrets = self.exercise(directory)
        self.assertEqual(events, [item for version in ('v1', 'v2') for item in
            ('fixture-fixture-' + version, 'prepare', 'django-start', 'go-start',
             'denials-' + version, 'browser-' + version, 'go-stop', 'django-stop',
             'inner-reset', 'outer-reset')])
        self.assertEqual(secrets, {'offline-token', 'offline-password'})
        self.assertEqual([row['version'] for row in report['fixtures']], ['v1', 'v2'])

    def test_partial_startup_and_child_proxy_browser_failures_stop_before_reset(self):
        for mode in ('prepare', 'django-start', 'go-start', 'denials',
                     'browser-child', 'proxy-cleanup', 'browser-cleanup'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                report, events, _ = self.exercise(directory, mode)
                self.assertLess(events.index('inner-reset'), events.index('outer-reset'))
                for owner in ('django', 'go'):
                    if owner + '-start' in events:
                        self.assertLess(events.index(owner + '-stop'), events.index('inner-reset'))
                self.assertEqual(len(report['fixtures']), 1)

    def test_unproved_service_exit_hard_exits_before_all_reset_and_preserves_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = '''
import json, sys
from pathlib import Path
from contextlib import contextmanager
from TESTS.adapters.django import message_mutations_browser_acceptance as c
p=Path(sys.argv[1])
@contextmanager
def owner(name):
 try: yield
 finally: (p/name).touch()
r=c.new_report();r['service_cleanup']['go']='cleanup_failed'
with owner('private-removed'),owner('outer-reset'),owner('inner-reset'):
 c.require_services_stopped_before_reset(r,p,lambda:(p/'failure.json').write_text(json.dumps(r)))
'''
            result = subprocess.run([sys.executable, '-c', program, str(root)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 124, result.stderr)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['failure.json'])
            receipt = json.loads((root / 'failure.json').read_text())
            self.assertEqual(receipt['recovery_directory'], str(root))
            self.assertFalse(receipt['success'])
            self.assertFalse(receipt['cleanup_verified'])

    def test_socket_measurement_uses_actual_hook_and_exact_loopback(self):
        guard = Mock(return_value=dict(pid=123, running=True, instance='owner'))
        connection = Mock()
        connection.sock.getsockname.return_value = ('127.0.0.1', 4567)
        with patch.object(cli.http.client, 'HTTPConnection', return_value=connection) as constructor:
            self.assertEqual(cli.measured_address(1234, guard), '127.0.0.1')
        constructor.assert_called_once_with('127.0.0.1', 1234, timeout=5)
        connection.close.assert_called_once()
        connection.sock.getsockname.return_value = ('127.0.0.2', 4567)
        with patch.object(cli.http.client, 'HTTPConnection', return_value=connection):
            with self.assertRaises(cli.CliError):
                cli.measured_address(1234, guard)


class PreflightControls(unittest.TestCase):
    def test_missing_input_is_rejected_without_service_or_output_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = []
            for name in cli.PATH_OPTIONS:
                flags += ['--' + name.replace('_', '-'), str(root / name)]
            with patch.object(cli, 'execute_private') as services, patch('sys.stdout', new=io.StringIO()) as output:
                code = cli.main(flags + ['--output', str(root / 'fresh')])
            self.assertEqual(code, 1)
            services.assert_not_called()
            self.assertFalse((root / 'fresh').exists())
            self.assertEqual(json.loads(output.getvalue()), {'success': False, 'errors': ['cli.failed']})
            self.assertNotIn(directory, output.getvalue())

    def test_shared_artifact_validators_run_before_service_entry(self):
        args = SimpleNamespace(**{name: Path('/offline') / name for name in (*cli.PATH_OPTIONS, 'node')},
                               timeout=360, deadline=1500, cleanup_grace=180)
        from TESTS.adapters.django import production_runtime
        for failing in ('frontend', 'build', 'native', 'modules', 'binaries', None):
            with self.subTest(failing=failing):
                expected = frozen_inputs()
                calls = []
                def check(stage, value):
                    def invoke(*args, **kw):
                        calls.append(stage)
                        if stage == failing:
                            raise RuntimeError('PRIVATE')
                        return copy.deepcopy(value)
                    return invoke
                with patch.object(production_runtime, 'require_production_python', return_value=expected['python']), \
                     patch.object(cli.browser, 'capture_provenance', check('frontend', expected['provenance'])), \
                     patch.object(cli.s, 'validate_build_record', check('build', expected['build'])), \
                     patch.object(cli.s, 'validate_go_build_record', check('native', expected['native'])), \
                     patch.object(cli.s, 'module_fingerprints', check('modules', expected['modules'])), \
                     patch.object(cli.s.runtime, 'service_binary_paths', return_value={'postgres': '/offline/bin/postgres'}), \
                     patch.object(cli.s.runtime, 'binary_hashes', check('binaries', expected['binaries'])), \
                     patch.object(cli, 'source_hashes', return_value=expected['sources']), \
                     patch.object(cli, 'execute_private') as services:
                    if failing is None:
                        frozen = cli.freeze_inputs(args)
                        self.assertEqual(frozen['paths'], {name: str(getattr(args, name)) for name in (*cli.PATH_OPTIONS, 'node')})
                        self.assertEqual(frozen['options'], expected['options'])
                    else:
                        with self.assertRaises(RuntimeError):
                            cli.freeze_inputs(args)
                    services.assert_not_called()
                self.assertEqual(calls, list(('frontend', 'build', 'native', 'modules', 'binaries'))[:len(calls)])

    def test_actual_stale_build_observation_fails_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = dict(success=True, exit_code=0, source_unchanged=True, build_inputs_unchanged=True,
                invocation=['vite', 'build'], source_sha256={'UI/index.html': DIGEST},
                artifact_sha256={'index.html': DIGEST})
            (root / 'build-record.json').write_text(json.dumps(record))
            observation = {key: record[key] for key in ('invocation', 'source_sha256', 'artifact_sha256')}
            observation['record_sha256'] = cli.s.sha(root / 'build-record.json')
            (root / 'build-observation.json').write_text(json.dumps(observation))
            args = SimpleNamespace(ui_dir=root, build_observation=root / 'build-observation.json',
                                   deadline=1500, cleanup_grace=180, timeout=360, output=root / 'fresh')
            provenance = dict(source_sha256={'UI/index.html': 'b' * 64},
                              artifact_sha256={'index.html': DIGEST})
            from TESTS.adapters.django import production_runtime
            with patch.object(production_runtime, 'require_production_python', return_value={}), \
                 patch.object(cli.browser, 'capture_provenance', return_value=provenance), \
                 patch.object(cli, 'execute_private') as services:
                self.assertEqual(cli.run(args), 1)
            services.assert_not_called()
            receipt = json.loads((args.output / 'results.json').read_text())
            self.assertFalse(receipt['success'])
            self.assertIn('preflight.failed', receipt['errors'])
            self.assertFalse(receipt['cleanup_verified'])

    def test_existing_output_is_not_modified_or_followed(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / 'existing'
                target.mkdir()
                receipt = target / 'results.json'
                receipt.write_text('previous-private-owner')
                output = root / 'link' if symlink else target
                if symlink:
                    output.symlink_to(target, target_is_directory=True)
                with patch.object(cli, 'freeze_inputs') as preflight, patch.object(cli, 'execute_private') as services:
                    with self.assertRaises(cli.s.HarnessError):
                        cli.run(SimpleNamespace(output=output))
                preflight.assert_not_called()
                services.assert_not_called()
                self.assertEqual(receipt.read_text(), 'previous-private-owner')

    def test_frozen_inputs_reject_option_path_source_and_artifact_drift(self):
        baseline = frozen_inputs()
        for key in baseline:
            changed = copy.deepcopy(baseline)
            changed[key] = {'changed': True}
            with self.subTest(key=key), patch.object(cli, 'freeze_inputs', return_value=changed):
                with self.assertRaises(cli.CliError):
                    cli.recheck_inputs(SimpleNamespace(), baseline)


class ExecutionControls(unittest.TestCase):
    def test_import_is_inert_even_when_process_and_socket_launches_are_forbidden(self):
        program = '''
import os, sys
from unittest.mock import patch
before=dict(os.environ)
with patch('subprocess.Popen', side_effect=AssertionError('process-at-import')), patch('socket.socket', side_effect=AssertionError('socket-at-import')):
 from TESTS.adapters.django import message_mutations_browser_acceptance
assert before == dict(os.environ)
assert 'django.conf' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', program], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_grace_hard_exit_survives_stalled_escalation_writer(self):
        program = '''
import signal, threading
from TESTS.adapters.django import message_mutations_browser_acceptance as c
control=c.ExecutionDeadline(1500,180,on_escalation=lambda state:c.bounded_publish(lambda:threading.Event().wait()))
control.grace_started=True
control._handle(signal.SIGALRM,None)
raise AssertionError('hard-exit-returned')
'''
        result = subprocess.run([sys.executable, '-c', program], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 124, result.stderr)

    def run_offline(self, directory, mode='normal'):
        root = Path(directory)
        args = SimpleNamespace(output=root / 'fresh', timeout=360, deadline=1500, cleanup_grace=180)
        template = complete_report()
        frozen = template['input_binding']
        writes, controls, seen_private = [], [], []
        original_environment, original_path = dict(os.environ), list(sys.path)
        original_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
        class Deadline(cli.ExecutionDeadline):
            def __enter__(self):
                controls.append(self)
                return super().__enter__()
            def __exit__(self, *exc):
                result = super().__exit__(*exc)
                if mode == 'late-cancellation':
                    self.reason = 'sigterm'
                return result
        def private(args, report, actual, private, secrets, publish):
            self.assertEqual(actual, frozen)
            self.assertTrue(private.is_dir())
            self.assertEqual(private.stat().st_mode & 0o777, 0o700)
            seen_private.append(private)
            report.update(copy.deepcopy(template))
            secrets.add('OFFLINE_PRIVATE')
            os.environ['OFFLINE_TEST_ONLY'] = 'changed'
            sys.path.append('/offline-mutated')
            if mode in ('failed-child', 'failed-proxy', 'failed-browser-cleanup'):
                report['versions'].pop()
                raise RuntimeError('OFFLINE_PRIVATE')
            if mode == 'runtime-cancellation':
                controls[0]._handle(signal.SIGTERM, None)
        def writer(path, report, secrets):
            writes.append(copy.deepcopy(report))
            if mode == 'publication-cancellation' and len(writes) == 1:
                controls[0]._handle(signal.SIGTERM, None)
            if mode in ('publication-failure', 'permanent-publication-failure') and len(writes) == 1:
                # A writer can replace the file then fail: invalidate it afterward.
                path.write_text(json.dumps(report))
                raise OSError('OFFLINE_PRIVATE')
            if mode == 'permanent-publication-failure':
                raise OSError('OFFLINE_PRIVATE')
            if mode == 'serialized-invalid' and len(writes) == 1:
                published = copy.deepcopy(report)
                published['versions'].pop()
                return original_writer(path, published, secrets)
            return original_writer(path, report, secrets)
        original_writer = cli.s.browser.oracle.write_publishable
        freeze_count = 0
        def freeze(args):
            nonlocal freeze_count
            freeze_count += 1
            if mode == 'preflight-failure':
                raise RuntimeError('OFFLINE_PRIVATE')
            changed = copy.deepcopy(frozen)
            if mode == 'before-start-drift' and freeze_count >= 2 or mode == 'after-source-drift' and freeze_count >= 3:
                changed['sources'][cli.OWNED[0]] = 'b' * 64
            return changed
        with patch.object(cli, 'freeze_inputs', freeze), patch.object(cli, 'execute_private', private), \
             patch.object(cli, 'source_hashes', return_value=frozen['sources']), \
             patch.object(cli, 'ExecutionDeadline', Deadline), \
             patch.object(cli.s.browser.oracle, 'write_publishable', writer):
            result = cli.run(args)
        receipt_path = args.output / 'results.json'
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
        self.assertEqual(dict(os.environ), original_environment)
        self.assertEqual(sys.path, original_path)
        self.assertTrue(all(not path.exists() for path in seen_private))
        self.assertIsNone(controls[0].token)
        self.assertEqual({sig: signal.getsignal(sig) for sig in original_handlers}, original_handlers)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertNotIn('OFFLINE_PRIVATE', json.dumps(receipt))
        return result, receipt, writes, seen_private

    def test_frozen_correct_inputs_and_success_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            result, receipt, writes, private = self.run_offline(directory)
        self.assertEqual(result, 0)
        self.assertTrue(cli.validate_report(receipt))
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(private), 1)

    def test_cli_rejects_invalid_actual_serialized_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            result, receipt, writes, _ = self.run_offline(directory, 'serialized-invalid')
        self.assertEqual(result, 1, 'in-memory validation cannot authorize a different serialized receipt')
        self.assertFalse(receipt['success'])
        self.assertFalse(writes[-1]['success'])

    def test_final_invalidation_after_handler_restoration_cannot_stall(self):
        program = '''
import copy, json, signal, sys, threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from TESTS.test_message_mutations_browser_acceptance import complete_report
from TESTS.adapters.django import message_mutations_browser_acceptance as c
template=complete_report()
original=c.s.browser.oracle.write_publishable
writes=0
class Deadline(c.ExecutionDeadline):
 def __exit__(self,*exc):
  result=super().__exit__(*exc)
  if sys.argv[2]=='restoration': self.reason='sigterm'
  return result
def private(args,report,*a): report.update(copy.deepcopy(template))
def writer(path,report,secrets):
 global writes
 writes+=1
 if writes>1:
  threading.Event().wait()
 result=original(path,report,secrets)
 if writes==1 and sys.argv[2]=='publication': signal.raise_signal(signal.SIGTERM)
 return result
args=SimpleNamespace(output=Path(sys.argv[1])/'fresh',deadline=20,cleanup_grace=float(sys.argv[3]))
with patch.object(c,'ExecutionDeadline',Deadline), patch.object(c,'freeze_inputs',return_value=template['input_binding']), patch.object(c,'source_hashes',return_value=template['source_sha256_before']), patch.object(c,'execute_private',private), patch.object(c.s.browser.oracle,'write_publishable',writer):
 c.run(args)
raise AssertionError('stalled invalidation returned')
'''
        for mode, grace in (('restoration', .2), ('publication', .2), ('restoration', 180)):
            with self.subTest(mode=mode, grace=grace), tempfile.TemporaryDirectory() as directory:
                try:
                    result = subprocess.run([sys.executable, '-c', program, directory, mode, str(grace)],
                                            capture_output=True, timeout=5)
                except subprocess.TimeoutExpired:
                    self.fail('final invalidation escaped the cleanup deadline and hung')
                self.assertEqual(result.returncode, 124, result.stderr)
                path = Path(directory) / 'fresh/results.json'
                if path.exists():
                    self.assertFalse(json.loads(path.read_bytes())['success'])

    def test_early_database_start_and_stop_failure_publishes_actual_recovery_path(self):
        from TESTS.adapters.django import historical_read_routes as owner
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / 'actual-runtime-work'
            work.mkdir()
            args = SimpleNamespace(output=root / 'fresh', deadline=20, cleanup_grace=5)
            frozen = frozen_inputs()
            original_mkdtemp = owner.tempfile.mkdtemp
            def allocate(*args, **kwargs):
                if kwargs.get('prefix') == 'az-history-':
                    return str(work)
                return original_mkdtemp(*args, **kwargs)
            def command(command, **kwargs):
                if command[-1] in ('start', 'stop'):
                    raise subprocess.CalledProcessError(1, command)
                return SimpleNamespace(returncode=0)
            with patch.object(cli, 'freeze_inputs', return_value=frozen), \
                 patch.object(cli, 'source_hashes', return_value=frozen['sources']), \
                 patch.object(owner.tempfile, 'mkdtemp', side_effect=allocate), \
                 patch.object(owner.shutil, 'which', side_effect=lambda name: '/offline/bin/' + name), \
                 patch.object(owner.subprocess, 'run', side_effect=command), \
                 patch.object(owner.subprocess, 'Popen', side_effect=AssertionError('no service launch')):
                self.assertEqual(cli.run(args), 1)
            receipt = json.loads((args.output / 'results.json').read_bytes())
            self.assertTrue(work.is_dir(), 'unproved live database directory must be retained')
            self.assertEqual(receipt['service_cleanup']['postgres'], 'cleanup_failed')
            self.assertEqual(receipt.get('recovery_directory'), str(work.resolve()))
            self.assertFalse(receipt['cleanup_verified'])

    def test_failed_children_and_source_drift_never_publish_success(self):
        for mode in ('failed-child', 'failed-proxy', 'failed-browser-cleanup',
                     'runtime-cancellation', 'after-source-drift', 'before-start-drift', 'preflight-failure'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                result, receipt, writes, private = self.run_offline(directory, mode)
                self.assertEqual(result, 1)
                self.assertFalse(receipt['success'])
                self.assertFalse(receipt['browser_acceptance'])
                self.assertFalse(receipt['http_denials_verified'])
                if mode in ('before-start-drift', 'preflight-failure'):
                    self.assertEqual(private, [])

    def test_late_cancellation_and_failed_publication_restore_handlers_and_invalidate(self):
        for mode in ('publication-cancellation', 'late-cancellation', 'publication-failure'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                result, receipt, writes, _ = self.run_offline(directory, mode)
                self.assertEqual(result, 1)
                self.assertFalse(receipt['success'])
                self.assertFalse(receipt['browser_acceptance'])
                self.assertFalse(receipt['http_denials_verified'])
                self.assertEqual(len(writes), 2)
                self.assertTrue(writes[0]['success'])
                self.assertFalse(writes[-1]['success'])

    def test_permanent_publication_failure_removes_candidate_success(self):
        with tempfile.TemporaryDirectory() as directory:
            result, receipt, writes, _ = self.run_offline(directory, 'permanent-publication-failure')
        self.assertEqual(result, 1)
        self.assertIsNone(receipt)
        self.assertTrue(writes[0]['success'])
        self.assertFalse(writes[-1]['success'])

    def test_escalation_writer_uses_daemon_and_bounded_join(self):
        thread = Mock()
        with patch.object(cli.threading, 'Thread', return_value=thread) as constructor:
            cli.bounded_publish(lambda: None)
        self.assertTrue(constructor.call_args.kwargs['daemon'])
        thread.start.assert_called_once()
        thread.join.assert_called_once_with(timeout=2)
        # The actual worker closure also swallows publication exceptions.
        def start(**kw):
            kw['target']()
            return thread
        with patch.object(cli.threading, 'Thread', side_effect=start):
            cli.bounded_publish(Mock(side_effect=OSError('PRIVATE')))


class PartialInitializationControls(unittest.TestCase):
    def test_real_database_owner_registers_redis_before_cancellation_delivery(self):
        from TESTS.adapters.django import historical_read_routes as owner
        from TESTS.adapters.go.message_image_execution import ExecutionInterrupted
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Mock()
            process.poll.return_value = None
            def launch(*args, **kwargs):
                signal.raise_signal(signal.SIGTERM)
                return process
            def stop(actual):
                self.assertIs(actual, process)
                process.poll.return_value = 0
            def command(command, **kwargs):
                return SimpleNamespace(returncode=3 if command[-1] == 'status' else 0)
            reservation = MagicMock()
            reservation.__enter__.return_value = reservation
            reservation.getsockname.return_value = ('127.0.0.1', 1234)
            report = cli.new_report()
            with patch.object(owner.shutil, 'which', side_effect=lambda name: '/offline/bin/' + name), \
                 patch.object(owner.subprocess, 'run', side_effect=command), \
                 patch.object(owner.subprocess, 'Popen', side_effect=launch), \
                 patch.object(owner.socket, 'socket', return_value=reservation), \
                 patch.object(owner, 'stop_process', side_effect=stop) as stopped, cli.ExecutionDeadline(20, 5):
                with self.assertRaises(ExecutionInterrupted):
                    with cli.s.private_database(root, report):
                        self.fail('cancelled Redis acquisition yielded')
            stopped.assert_called_once_with(process)
            self.assertEqual(report['service_cleanup']['redis'], 'stopped')
            self.assertTrue(report['owned_directory_removed'])
            self.assertNotIn('recovery_directory', report)

    def test_partial_setup_and_independent_finalizers_preserve_primary_failure(self):
        for mode in ('app-ready-fails', 'database-close-fails', 'redis-close-fails', 'cache-close-fails'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                events = []
                primary = RuntimeError('private-primary-error')
                settings = SimpleNamespace(DATABASES={'default': {}}, MEDIA_ROOT=root)
                connection = MagicMock()
                connection.get_connection_params.return_value = {'connect_timeout': 5}
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchone.side_effect = [('30s',), ('5s',)]
                connections = Mock()
                connections.__getitem__ = Mock(return_value=connection)
                close_count = 0
                def close_database():
                    nonlocal close_count
                    close_count += 1
                    events.append('close-db')
                    if mode == 'database-close-fails' and close_count >= 2:
                        raise RuntimeError('private-db-close-error')
                connections.close_all.side_effect = close_database
                raw = Mock()
                raw.connection_pool.connection_kwargs = dict(socket_timeout=5, socket_connect_timeout=5)
                def close_redis():
                    events.append('close-redis')
                    if mode == 'redis-close-fails':
                        raise RuntimeError('private-redis-close-error')
                raw.close.side_effect = close_redis
                caches = MagicMock()
                caches.__getitem__.return_value.client.get_client.return_value = raw
                def close_cache():
                    events.append('close-cache')
                    if mode == 'cache-close-fails':
                        raise RuntimeError('private-cache-close-error')
                caches.close_all.side_effect = close_cache
                @contextmanager
                def database(private, report):
                    events.append('database-enter')
                    try:
                        yield root, 1234, io.StringIO()
                    finally:
                        events.append('database-exit')
                @contextmanager
                def override(**kw):
                    yield
                def initialize():
                    events.append('app-ready')
                    self.assertEqual(settings.DATABASES['default']['OPTIONS']['connect_timeout'], 5)
                    self.assertEqual(settings.CACHES['default']['OPTIONS']['SOCKET_TIMEOUT'], 5)
                    if mode == 'app-ready-fails':
                        raise primary
                    return 'apps', settings
                modules = {
                    'django.conf': SimpleNamespace(settings=settings),
                    'django.conf.global_settings': SimpleNamespace(PASSWORD_HASHERS=['production']),
                    'django.test.utils': SimpleNamespace(override_settings=override),
                    'django.db': SimpleNamespace(connections=connections),
                    'django.core.cache': SimpleNamespace(caches=caches),
                    'redis': SimpleNamespace(Redis=Mock(return_value=raw), ConnectionError=ConnectionError),
                    'TESTS.http_client': SimpleNamespace(poll=lambda check, *a, **kw: check()),
                }
                report = cli.new_report()
                original_path = list(sys.path)
                try:
                    with patch.dict(sys.modules, modules), patch.dict(os.environ, {}, clear=True), \
                         patch.object(cli.s, 'private_database', database), \
                         patch.object(cli.s.runtime, 'private_environment', return_value=({}, set())), \
                         patch.object(cli.s.runtime, 'initialize_django_apps', initialize), \
                         patch.object(cli.s.runtime, 'migrate_full_schema', side_effect=primary):
                        with self.assertRaises(RuntimeError) as error:
                            cli.execute_private(SimpleNamespace(deadline=1500, cleanup_grace=180),
                                report, frozen_inputs(), root, set(), Mock())
                    self.assertIs(error.exception, primary)
                finally:
                    sys.path[:] = original_path
                self.assertIn('close-db', events)
                caches.close_all.assert_called_once()
                self.assertEqual(events[-1], 'database-exit')
                if mode == 'app-ready-fails':
                    raw.close.assert_not_called()
                else:
                    raw.close.assert_called_once()
                    self.assertEqual(close_count, 2)
                    self.assertEqual(len(report['cleanup_errors']), 1)


if __name__ == '__main__':
    unittest.main()
