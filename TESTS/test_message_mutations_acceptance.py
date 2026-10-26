"""Offline rejection controls. These receipts never establish runtime parity."""
import copy
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import tempfile
import sys
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from TESTS.contracts import test_message_mutations as contract
from TESTS.adapters.django import message_mutations_acceptance as cli


class AcceptanceControls(unittest.TestCase):
    def setUp(self):
        self.source = contract.load_source()
        self.hashes = {'BE_GO/go.mod': 'a' * 64, 'TESTS/fixtures/message_mutations.json': 'b' * 64}
        steps = lambda names: {'verified': True, 'results': dict.fromkeys(names, True)}
        self.report = {
            'success': False, 'versioned': False, 'errors': [], 'runtime_executed': True,
            'complete_message_family_parity': False, 'source_sha256': self.hashes,
            'source_sha256_after': self.hashes, 'build_source_sha256': self.hashes,
            'go_build_source_sha256': self.hashes, 'source_unchanged': True,
            'python': {'python': cli.production_python_version(), 'sha256': 'c' * 64},
            'go_toolchain': {'required': '1.25.12', 'actual': '1.25.12', 'local': True},
            'schema': {'applied_migrations': 547, 'remaining_plan': 0, 'mode': 'full-migration-history'},
            'audit_resolvers_after_migration': True,
            'service_binary_sha256': dict.fromkeys(cli.TOOLS, 'd' * 64),
            'go_artifact': {'server_sha256': 'e' * 64, 'codec_sha256': 'f' * 64,
                            'process_exit': 0},
            'artifact_after_sha256': {'server_sha256': 'e' * 64, 'codec_sha256': 'f' * 64},
            'service_cleanup': dict.fromkeys(cli.SERVICES, 'stopped'),
            'owned_directory_removed': True, 'private_directory_removed': True,
            'environment_restored': True, 'umask_restored': True,
            'resource_cleanup': steps(('redis_client', 'db_connections', 'cache_override')),
            'sensitive_cleanup': {**steps(cli.SENSITIVE), 'errors': []},
            'evidence': {'verified': True, 'cases_per_workload': 119,
                         'normalized_effects_sha256': '1' * 64, 'labels': ['django-native', 'go-native']},
            'client_measurements': {
                label: {'transport': 'native loopback TCP', 'address': '127.0.0.1', 'status': 401}
                for label in ('django-native', 'go-native')},
        }
        self.report['service_cleanup'].update({'caddy': 'not_started', 'caddy-misroute': 'not_started'})
        workloads = []
        for label, backend in (('django-native', 'django'), ('go-native', 'go')):
            workloads.append({'label': label, 'backend': backend, 'success': True,
                'normalized_effects_sha256': '1' * 64,
                'go_binary_sha256': 'e' * 64 if backend == 'go' else None,
                'cases': [{'case': case['name'], 'status': case['status'], 'models': 128,
                           'checks': dict.fromkeys(('response', 'state', 'audit', 'cache', 'attribution'), True),
                           'state_before_sha256': '2' * 64, 'state_after_sha256': '3' * 64,
                           'cache_sha256': '4' * 64} for case in self.source['cases']]})
        self.report['runtime'] = {'success': True, 'runtime_executed': True,
            'differential_executed': True, 'django_only': False, 'complete_message_family_parity': False,
            'fixture_cleanup_verified': True, 'authored_source_sha256': contract.source_digest(self.source),
            'source_sha256': self.hashes, 'source_sha256_after': self.hashes,
            'workloads': workloads, 'fixture_cleanup': [steps(cli.FIXTURE) for _ in workloads]}

    def validate(self, report=None):
        with patch.object(cli, 'source_hashes', return_value=self.hashes), \
                patch.object(cli, 'required_go_version', return_value='1.25.12'):
            cli.validate_report(self.report if report is None else report)

    def test_control_receipt_is_complete_even_without_top_success_flag(self):
        self.validate()

    def test_public_receipt_remains_revalidatable_and_excludes_private_diagnostics(self):
        self.report['postgres_startup_diagnostics'] = {'path': 'PRIVATE-CREDENTIAL'}
        self.report['runtime']['schema'] = {'sql': 'PRIVATE-CREDENTIAL'}
        self.report['sensitive_cleanup']['errors'] = ['PRIVATE-CREDENTIAL']
        public = cli.public_receipt(self.report)
        self.assertNotIn('PRIVATE-CREDENTIAL', json.dumps(public))
        self.assertEqual(public['sensitive_cleanup']['errors'], ['cleanup.private-failed'])
        public['sensitive_cleanup']['errors'] = []
        self.validate(public)

    def test_raw_observation_controls_reject_claimed_checks_and_matching_hashes(self):
        from TESTS.adapters.django import message_mutations as adapter
        from TESTS.adapters.django.historical_read_cache import HistoricalReadCacheSnapshot
        from types import MappingProxyType
        case = self.source['cases'][0]
        state = {f'synthetic.Model{number}': [] for number in range(128)}
        receipt = {'case': case['name'], 'status': case['status'], 'models': 128,
                   'state_before_sha256': adapter.shared.digest(state),
                   'state_after_sha256': adapter.shared.digest(state),
                   'cache_sha256': HistoricalReadCacheSnapshot(MappingProxyType({})).digest}
        observation = {'case': case['name'], 'status': case['status'], 'before': state, 'after': state,
                       'cache_before': [], 'cache_after': [], 'observed': []}
        endpoint = {'label': 'django-native', 'backend': 'django', 'remote_addr': '127.0.0.1'}
        runtime = {'workloads': [{'label': 'django-native', 'backend': 'django',
            'normalized_effects_sha256': 'a' * 64, 'cases': [receipt]}]}
        # Each corrupts a different raw domain, despite the receipt claiming checks passed.
        for change, code in (
                (lambda value: value.update(case='invented'), 'evidence.case'),
                (lambda value: value['after'].update({'synthetic.Model0': [{'id': 1}]}), 'evidence.state'),
                (lambda value: value['cache_after'].append({'key_hex': 'ff'}), 'evidence.cache'),
                (lambda value: None, 'complete ASGI delta differs')):
            value = copy.deepcopy(observation)
            change(value)
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                path = work / 'observations-0.jsonl'
                path.write_text(json.dumps(value) + '\n')
                path.chmod(0o600)
                with self.assertRaisesRegex((cli.AcceptanceError, AssertionError), code):
                    cli.verify_observations(runtime, [endpoint], work, started=0, finished=1)

    def test_actual_effect_projection_rejects_consistent_hashes_of_wrong_business_effect(self):
        from TESTS.adapters.django import message_mutations as adapter
        from TESTS.adapters.django.historical_read_cache import HistoricalReadCacheSnapshot
        from types import MappingProxyType
        case = self.source['cases'][0]  # Authored CREATE requires a new Message and audit/index.
        state = {f'synthetic.Model{number}': [] for number in range(125)}
        state.update({contract.MESSAGE: [], contract.AUDIT: [], contract.DELIVERY: []})
        observation = {'case': case['name'], 'status': case['status'], 'before': state, 'after': state,
            'cache_before': [], 'cache_after': [], 'observed': [{'seq': 1, 'method': case['method'],
                'path': case['path'], 'cid': case['name'], 'status': case['status'], 'error_type': None}]}
        receipt = {'case': case['name'], 'status': case['status'], 'models': 128,
            'state_before_sha256': adapter.shared.digest(state), 'state_after_sha256': adapter.shared.digest(state),
            'cache_sha256': HistoricalReadCacheSnapshot(MappingProxyType({})).digest}
        workload = {'label': 'django-native', 'backend': 'django', 'cases': [receipt],
                    'normalized_effects_sha256': adapter.shared.digest([{'kind': 'none'}])}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'observations-0.jsonl'
            path.write_text(json.dumps(observation) + '\n')
            path.chmod(0o600)
            with self.assertRaisesRegex(AssertionError, 'state.one-created-message'):
                cli.verify_observations({'workloads': [workload]},
                    [{'label': 'django-native', 'backend': 'django', 'remote_addr': '127.0.0.1'}],
                    Path(directory), started=0, finished=1)

    def test_missing_extra_duplicate_reordered_or_renamed_cases_fail(self):
        for operation in (
                lambda cases: cases.pop(),
                lambda cases: cases.append(copy.deepcopy(cases[0])),
                lambda cases: cases.__setitem__(1, copy.deepcopy(cases[0])),
                lambda cases: cases.reverse(),
                lambda cases: cases[0].update(case='message-mutations-invented')):
            with self.subTest(operation=operation):
                report = copy.deepcopy(self.report)
                operation(report['runtime']['workloads'][0]['cases'])
                with self.assertRaises(cli.AcceptanceError):
                    self.validate(report)

    def test_matching_success_flags_cannot_hide_corrupt_evidence(self):
        mutations = [
            lambda r: r['runtime']['workloads'][0]['cases'][0].update(status=500),
            lambda r: r['runtime']['workloads'][0]['cases'][0]['checks'].update(audit=False),
            lambda r: r['runtime']['workloads'][0]['cases'][0]['checks'].pop('attribution'),
            lambda r: r['runtime']['workloads'][0]['cases'][0].update(models=127),
            lambda r: r['runtime']['workloads'][0].update(backend='go'),
            lambda r: r['runtime']['workloads'][1].update(go_binary_sha256='0' * 64),
            lambda r: r['runtime']['workloads'][1].update(normalized_effects_sha256='0' * 64),
            lambda r: r['runtime'].update(authored_source_sha256='0' * 64),
            lambda r: r['runtime'].update(source_sha256_after={'changed': '0' * 64}),
            lambda r: r.update(build_source_sha256={'changed': '0' * 64}),
            lambda r: r.update(go_build_source_sha256={'changed': '0' * 64}),
            lambda r: r['artifact_after_sha256'].update(codec_sha256='0' * 64),
            lambda r: r['runtime'].pop('fixture_cleanup_verified'),
            lambda r: r['runtime']['fixture_cleanup'][0]['results'].update(sequences=False),
            lambda r: r['service_cleanup'].update(redis='running'),
            lambda r: r.update(environment_restored=False),
            lambda r: r['resource_cleanup']['results'].update(cache_override=False),
            lambda r: r.update(private_directory_removed=False),
            lambda r: r['evidence'].update(verified=False),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(control=index):
                report = copy.deepcopy(self.report)
                report['success'] = True
                mutate(report)
                with self.assertRaises(cli.AcceptanceError):
                    self.validate(report)

    def versioned(self):
        self.report['versioned'] = True
        self.report['service_binary_sha256']['docker'] = 'd' * 64
        self.report['service_cleanup'].update({'caddy': 'stopped', 'caddy-misroute': 'stopped'})
        self.report['caddy_image_sha256'] = '5' * 64
        self.report['misroute_control'] = {'rejected': True, 'actual_backend': 'django', 'status': 401,
                                          'state_and_cache_unchanged': True, 'process_verified': True}
        for index, label in enumerate(('caddy-v1', 'caddy-v2')):
            workload = copy.deepcopy(self.report['runtime']['workloads'][index])
            workload['label'] = label
            self.report['runtime']['workloads'].append(workload)
            self.report['runtime']['fixture_cleanup'].append(copy.deepcopy(self.report['runtime']['fixture_cleanup'][0]))
        self.report['evidence']['labels'] += ['caddy-v1', 'caddy-v2']
        for label in ('caddy-v1', 'caddy-v2'):
            self.report['client_measurements'][label] = {'transport': 'proxy-owned health remote_host',
                'address': '192.0.2.1', 'status': 200}

    def test_versioned_requires_both_routes_and_real_misroute_rejection(self):
        self.versioned()
        self.validate()
        for mutate in (
                lambda r: r['runtime']['workloads'].pop(),
                lambda r: r['misroute_control'].update(rejected=False),
                lambda r: r['misroute_control'].update(rejected=1),
                lambda r: r['misroute_control'].update(actual_backend='go'),
                lambda r: r['misroute_control'].update(process_verified=False),
                lambda r: r['service_cleanup'].update(**{'caddy-misroute': 'running'})):
            report = copy.deepcopy(self.report)
            mutate(report)
            with self.assertRaises(cli.AcceptanceError):
                self.validate(report)

    def test_go_preflight_uses_owned_environment_and_rejects_old_or_auto_toolchain(self):
        for output in ('go version go1.25.11 darwin/arm64\n', 'go version go1.26rc1 darwin/arm64\n', 'private-token'):
            with patch.object(cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')) as run:
                with self.assertRaises(cli.AcceptanceError):
                    cli.check_go_toolchain({'PATH': '/owned', 'GOTOOLCHAIN': 'local', 'GOPROXY': 'off'})
                self.assertEqual(run.call_args.kwargs['env']['GOTOOLCHAIN'], 'local')
                self.assertEqual(run.call_args.kwargs['cwd'], cli.ROOT / 'BE_GO')
        with patch.object(cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'go version go1.25.12 darwin/arm64\n', '')):
            self.assertEqual(cli.check_go_toolchain({'GOTOOLCHAIN': 'local'})['actual'], '1.25.12')

    def test_failure_restores_environment_umask_and_removes_private_evidence(self):
        original = dict(os.environ)
        directories = []
        def fail(*args, **kwargs):
            private = args[2]
            directories.append(private)
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((private / 'runtime.log').stat().st_mode), 0o600)
            (private / 'secret').write_text('PRIVATE-CREDENTIAL')
            os.environ.clear()
            os.environ['SECRET'] = 'PRIVATE-CREDENTIAL'
            raise RuntimeError('PRIVATE-CREDENTIAL')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cli, 'preflight', return_value={}), \
                patch.object(cli, 'source_hashes', return_value=self.hashes), \
                patch.object(cli, 'execute_private', side_effect=fail), redirect_stdout(io.StringIO()) as out:
            output = Path(directory) / 'fresh'
            before_mask = os.umask(0o077)
            os.umask(before_mask)
            status = cli.main(['--output', str(output)])
            after_mask = os.umask(0o077)
            os.umask(after_mask)
            self.assertEqual(before_mask, after_mask)
            self.assertEqual(status, 1)
            receipt = json.loads((output / 'results.json').read_text())
            self.assertTrue(receipt['environment_restored'])
            self.assertTrue(receipt['private_directory_removed'])
            self.assertEqual(set(path.name for path in output.iterdir()), {'results.json'})
            self.assertNotIn('PRIVATE-CREDENTIAL', (output / 'results.json').read_text() + out.getvalue())
        self.assertEqual(dict(os.environ), original)
        self.assertTrue(directories)
        self.assertTrue(all(not path.exists() for path in directories))

    def test_go_failure_happens_before_service_setup(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cli, 'require_production_python', return_value={'python': cli.production_python_version()}), \
                patch.object(cli, 'check_go_toolchain', side_effect=cli.AcceptanceError('preflight.go-version')), \
                patch.object(cli, 'execute_private') as execute, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--output', str(Path(directory) / 'fresh')]), 1)
            execute.assert_not_called()

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            marker = Path(directory) / 'results.json'
            marker.write_text('keep')
            with patch.object(cli, 'preflight') as setup:
                self.assertEqual(cli.main(['--output', directory]), 1)
                setup.assert_not_called()
            self.assertEqual(marker.read_text(), 'keep')

    def test_cli_exit_is_derived_from_full_validation(self):
        for corrupt in (False, True):
            template = copy.deepcopy(self.report)
            template['success'] = True
            if corrupt:
                template['runtime']['workloads'][0]['cases'][0]['checks']['audit'] = False
            def complete(report, *_args):
                report.update(template)
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(cli, 'preflight', return_value={}), \
                    patch.object(cli, 'execute_private', side_effect=complete), \
                    patch.object(cli, 'source_hashes', return_value=self.hashes), \
                    patch.object(cli, 'required_go_version', return_value='1.25.12'), \
                    redirect_stdout(io.StringIO()):
                output = Path(directory) / 'fresh'
                status = cli.main(['--output', str(output)])
                receipt = json.loads((output / 'results.json').read_text())
                self.assertEqual(status, 1 if corrupt else 0)
                self.assertEqual(receipt['success'], not corrupt)
                if not corrupt:
                    self.validate(receipt)


class IndependentReviewControls(unittest.TestCase):
    """Offline failure-path probes; no Docker command or database is executed."""

    def probe(self, script, *args):
        result = subprocess.run([sys.executable, '-B', '-c', textwrap.dedent(script), *args],
                                cwd=cli.ROOT, capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_native_child_stderr_is_private_and_fds_restore_after_failure(self):
        result = self.probe(r'''
            import json, os, subprocess, sys, tempfile
            from pathlib import Path
            from unittest.mock import patch
            from TESTS.adapters.django import message_mutations_acceptance as cli
            seen = []
            def execute(report, tools, private, log):
                subprocess.run([sys.executable, '-B', '-c',
                    "import os; os.write(2, b'CHILD_STDERR_SECRET_SENTINEL\\n'); os.write(1, b'CHILD_STDOUT_SECRET_SENTINEL\\n')"], check=True)
                log.flush()
                content = (private / 'runtime.log').read_text()
                seen.append('CHILD_STDERR_SECRET_SENTINEL' in content and 'CHILD_STDOUT_SECRET_SENTINEL' in content)
                raise RuntimeError('PRIVATE_EXCEPTION_SENTINEL')
            with tempfile.TemporaryDirectory() as directory, patch.object(cli, 'preflight', return_value={}), \
                    patch.object(cli, 'source_hashes', return_value={}), patch.object(cli, 'execute_private', side_effect=execute):
                output = Path(directory) / 'fresh'
                code = cli.main(['--output', str(output)])
                receipt = (output / 'results.json').read_text()
                assert code == 1 and seen == [True], seen
                assert 'SENTINEL' not in receipt
            os.write(1, b'FD1_RESTORED\n')
            os.write(2, b'FD2_RESTORED\n')
        ''')
        self.assertNotIn('SECRET_SENTINEL', result.stdout + result.stderr)
        self.assertNotIn('PRIVATE_EXCEPTION_SENTINEL', result.stdout + result.stderr)
        self.assertIn('FD1_RESTORED', result.stdout)
        self.assertEqual(result.stderr, 'FD2_RESTORED\n')

    def test_fd_restore_even_when_log_flush_fails(self):
        self.probe('''
            import os, tempfile
            from TESTS.adapters.django import message_mutations_acceptance as cli
            class BrokenLog:
                def __init__(self, real): self.real = real
                def fileno(self): return self.real.fileno()
                def flush(self): raise RuntimeError('flush-failed')
            before = [os.fstat(fd) for fd in (1, 2)]
            with tempfile.TemporaryFile(mode='w+') as log:
                try:
                    with cli.private_diagnostics(BrokenLog(log)): pass
                except RuntimeError: pass
                else: raise AssertionError('flush failure swallowed')
            assert [os.fstat(fd) for fd in (1, 2)] == before
        ''')

    def test_partial_initialize_failure_closes_connections_independently(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        for close_fails in (False, True):
            events = []
            report = {'versioned': False}
            @contextmanager
            def database(*args):
                try:
                    yield Path('/unused-owned'), 1, None
                finally:
                    events.append('database-finalized')
            def initialize():
                events.append('opened-during-init')
                raise RuntimeError('partial-init')
            def close():
                events.append('connections-closed')
                if close_fails:
                    raise RuntimeError('close-failed')
            db = SimpleNamespace(connections=SimpleNamespace(close_all=close))
            with patch.object(reuse, 'owned_message_database', database), \
                    patch.object(reuse, 'private_environment', return_value=({}, set())), \
                    patch.object(reuse, 'apply_private_environment'), \
                    patch.object(reuse, 'initialize_django_apps', side_effect=initialize), \
                    patch.dict(sys.modules, {'django.db': db}), \
                    self.assertRaisesRegex(RuntimeError, 'partial-init'):
                cli.execute_private(report, {}, Path('/unused-private'), io.StringIO())
            self.assertEqual(events, ['opened-during-init', 'connections-closed', 'database-finalized'])
            self.assertEqual(report['resource_cleanup']['results'],
                {'redis_client': True, 'db_connections': not close_fails, 'cache_override': True})
            self.assertEqual(report['resource_cleanup']['verified'], not close_fails)

    def test_missing_pinned_image_rejects_before_any_services(self):
        from TESTS.adapters.django import message_reads_runtime as reuse
        for present in (False, True):
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(cli, 'require_production_python', return_value={'python': cli.production_python_version()}), \
                    patch.object(cli, 'check_go_toolchain', return_value={}), \
                    patch.object(reuse, 'service_binary_paths', return_value={}), \
                    patch.object(reuse, 'apply_private_environment'), \
                    patch.object(cli, 'source_hashes', return_value={}), \
                    patch.object(cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0 if present else 1,
                        'sha256:' + 'a' * 64 + '\n' if present else '', 'PRIVATE-IMAGE-DIAGNOSTIC')) as run, \
                    patch.object(cli, 'execute_private', side_effect=RuntimeError('offline-stop')) as execute, \
                    redirect_stdout(io.StringIO()):
                output = Path(directory) / 'fresh'
                self.assertEqual(cli.main(['--versioned', '--output', str(output)]), 1)
                self.assertEqual(execute.call_count, int(present))
                self.assertEqual(run.call_count, 1)
                args = run.call_args.args[0]
                self.assertEqual(args[:5], ['docker', 'image', 'inspect', '--format', '{{.Id}}'])
                pinned = re.search(r'^FROM (caddy:\S+) AS production$',
                    (cli.ROOT / 'UI/Dockerfile').read_text(), re.M)[1]
                self.assertEqual(args[-1], pinned)
                self.assertEqual(run.call_args.kwargs['timeout'], 15)
                self.assertNotIn('PRIVATE-IMAGE-DIAGNOSTIC', (output / 'results.json').read_text())

    def test_no_pull_policy_forwards_and_preserves_default(self):
        from TESTS.adapters import caddy
        from TESTS.adapters.django import message_reads_runtime as reuse
        for policy in (None, 'never'):
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                value = b''
                if args[1] == 'port': value = b'127.0.0.1:43210'
                if args[1:3] == ['inspect', '--format']:
                    value = json.dumps({'container_id': 'a' * 64, 'image_id': 'sha256:' + 'b' * 64}).encode()
                return subprocess.CompletedProcess(args, 1 if args[1] == 'inspect' and len(args) == 3 else 0, value)
            with patch.object(caddy.subprocess, 'run', side_effect=run), patch.object(caddy, 'poll'):
                with reuse.owned_caddy({}, io.StringIO(), 'django', 'go', pull_policy=policy): pass
            command = next(args for args in calls if args[1] == 'run')
            self.assertEqual('--pull=never' in command, policy == 'never')
            self.assertFalse(any(args[1] == 'pull' for args in calls))

    def test_database_bounds_precede_migration_and_locked_snapshot(self):
        # A cursor models a lock refusal only when the configured session has a
        # lock bound. This is ordering/parameter evidence, not a PostgreSQL run.
        self.probe('''
            import io, os, sys
            from contextlib import contextmanager
            from pathlib import Path
            from types import SimpleNamespace
            from unittest.mock import patch
            from TESTS.adapters.django import message_mutations_acceptance as cli
            from TESTS.adapters.django import message_reads_runtime as reuse
            events = []
            state = {}
            @contextmanager
            def cursor():
                def execute(sql):
                    events.append(sql)
                    if sql.startswith('SET '):
                        key, value = sql[4:].split(' = ')
                        state[key] = int(value)
                yield SimpleNamespace(execute=execute)
            connection = SimpleNamespace(settings_dict={'NAME': 'assozeta_contract_probe'}, cursor=cursor,
                ops=SimpleNamespace(quote_name=lambda name: '\"' + name + '\"'))
            @contextmanager
            def database(*args):
                try: yield Path('/unused'), 1, None
                finally: events.append('database-finalized')
            def initialize():
                assert os.environ['PGCONNECT_TIMEOUT'] == '5'
                assert 'statement_timeout=30000' in os.environ['PGOPTIONS']
                assert 'lock_timeout=5000' in os.environ['PGOPTIONS']
                return None, None
            def migrate(log):
                assert state == {'statement_timeout': 30000, 'lock_timeout': 5000}, state
                assert connection.settings_dict['OPTIONS']['connect_timeout'] == 5
                assert len([sql for sql in events if sql.startswith('ALTER DATABASE')]) == 2
                # A held-lock snapshot would be refused by this session bound.
                events.append('locked-query-refused')
                raise TimeoutError('lock-timeout')
            class Override:
                def enable(self): pass
                def disable(self): events.append('override-disabled')
            db = SimpleNamespace(connections={'default': connection})
            class Connections(dict):
                def close_all(self): events.append('connections-closed')
            db.connections = Connections(default=connection)
            with patch.object(reuse, 'owned_message_database', database), \
                 patch.object(reuse, 'private_environment', return_value=({}, set())), \
                 patch.object(reuse, 'initialize_django_apps', side_effect=initialize), \
                 patch.object(reuse, 'migrate_full_schema', side_effect=migrate), \
                 patch.dict(sys.modules, {'django.db': db, 'django.test.utils': SimpleNamespace(override_settings=lambda **kw: Override())}):
                report = {'versioned': False}
                try: cli.execute_private(report, {}, Path('/unused'), io.StringIO())
                except TimeoutError: pass
                else: raise AssertionError('expected lock timeout')
            assert events[-4:] == ['locked-query-refused', 'connections-closed', 'override-disabled', 'database-finalized'], events
            assert report['resource_cleanup']['verified']
        ''')

    def test_deadline_sigterm_startup_and_cleanup_cancel_without_owned_child_leak(self):
        for mode in ('deadline', 'sigterm', 'startup', 'cleanup', 'cleanup-failed'):
            result = self.probe('''
                import io, json, os, signal, subprocess, sys, tempfile, time
                from contextlib import contextmanager
                from pathlib import Path
                from unittest.mock import patch
                from TESTS.adapters.django import message_mutations_acceptance as cli
                mode = sys.argv[1]
                children, events = [], []
                @contextmanager
                def owned(report):
                    process = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(20)'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    children.append(process)
                    report['service_cleanup']['go'] = 'running'
                    try:
                        if mode == 'startup':
                            os.kill(os.getpid(), signal.SIGTERM)
                            events.append('startup-finished')
                        yield process
                    finally:
                        if mode == 'cleanup': os.kill(os.getpid(), signal.SIGTERM)
                        # Repeated signal must not interrupt or restart teardown.
                        os.kill(os.getpid(), signal.SIGTERM)
                        process.terminate()
                        process.wait(timeout=2)
                        events.append('child-reaped')
                        report['service_cleanup']['go'] = 'cleanup_failed' if mode == 'cleanup-failed' else 'stopped'
                def execute(report, tools, private, log):
                    with cli._owned(owned(report)):
                        if mode in ('sigterm', 'cleanup-failed'): os.kill(os.getpid(), signal.SIGTERM)
                        if mode == 'deadline':
                            # Offline stand-in for a blocked synchronous query.
                            while True: time.sleep(0.005)
                    events.append('after-owned')
                try:
                    with tempfile.TemporaryDirectory() as directory, patch.object(cli, 'preflight', return_value={}), \
                            patch.object(cli, 'source_hashes', return_value={}), patch.object(cli, 'execute_private', side_effect=execute):
                        output = Path(directory) / 'fresh'
                        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
                        code = cli.main(['--output', str(output), '--deadline', '0.08', '--cleanup-grace', '2'])
                        receipt = json.loads((output / 'results.json').read_text())
                        assert code == 1 and receipt['success'] is False, receipt
                        assert receipt['service_cleanup']['go'] == ('cleanup_failed' if mode == 'cleanup-failed' else 'stopped')
                        assert receipt['private_evidence'] == 'cleanup-unverified'
                        assert receipt['private_directory_removed'] is True
                        assert 'execution.interrupted' in receipt['errors'], receipt
                        assert len(children) == 1 and children[0].poll() is not None
                        assert 'child-reaped' in events
                        if mode == 'startup': assert 'startup-finished' in events and 'after-owned' not in events
                        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
                        assert {sig: signal.getsignal(sig) for sig in previous} == previous
                finally:
                    for process in children:
                        if process.poll() is None: process.kill(); process.wait(timeout=2)
            ''', mode)
            self.assertNotIn('Traceback', result.stdout + result.stderr)

    def test_sigterm_unwinds_existing_finally_before_process_exit(self):
        # Uses only the original CLI arguments; also demonstrates the original
        # default SIGTERM termination bypassing Python finally on the RED source.
        script = r'''
            import signal, sys
            from pathlib import Path
            from unittest.mock import patch
            from TESTS.adapters.django import message_mutations_acceptance as cli
            marker = Path(sys.argv[2])
            def execute(report, tools, private, log):
                marker.touch()
                try:
                    signal.raise_signal(signal.SIGTERM)
                finally:
                    marker.unlink()
            with patch.object(cli, 'preflight', return_value={}), patch.object(cli, 'source_hashes', return_value={}), \
                    patch.object(cli, 'execute_private', side_effect=execute):
                code = cli.main(['--output', sys.argv[1]])
                assert code == 1
        '''
        with tempfile.TemporaryDirectory() as directory:
            output, marker = Path(directory) / 'fresh', Path(directory) / 'owned-resource'
            result = subprocess.run([sys.executable, '-B', '-c', textwrap.dedent(script), str(output), str(marker)],
                                    cwd=cli.ROOT, capture_output=True, timeout=4)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(marker.exists(), 'SIGTERM bypassed resource finally')
            receipt = json.loads((output / 'results.json').read_text())
            self.assertFalse(receipt['success'])
            self.assertTrue(receipt['private_directory_removed'])
            self.assertIn('execution.interrupted', receipt['errors'])

    def test_cancellation_during_umask_transition_restores_process_state(self):
        self.probe('''
            import io, json, os, signal, tempfile
            from contextlib import redirect_stdout
            from pathlib import Path
            from unittest.mock import patch
            from TESTS.adapters.django import message_mutations_acceptance as cli
            real_umask = os.umask
            original = real_umask(0o022)
            real_umask(original)
            calls = []
            def interrupted_umask(value):
                previous = real_umask(value)
                calls.append(value)
                if len(calls) == 1: signal.raise_signal(signal.SIGTERM)
                return previous
            with tempfile.TemporaryDirectory() as directory, patch.object(cli.os, 'umask', side_effect=interrupted_umask), \
                    patch.object(cli, 'source_hashes', return_value={}), patch.object(cli, 'preflight') as preflight, \
                    redirect_stdout(io.StringIO()):
                output = Path(directory) / 'fresh'
                assert cli.main(['--output', str(output)]) == 1
                receipt = json.loads((output / 'results.json').read_text())
                assert receipt['success'] is False and receipt['umask_restored'] is True
                assert 'execution.interrupted' in receipt['errors']
                preflight.assert_not_called()
            after = real_umask(original)
            assert after == original
        ''')

    def test_cleanup_grace_escalation_cannot_publish_verified_cleanup(self):
        script = r'''
            import signal, sys, time
            from pathlib import Path
            from unittest.mock import patch
            from TESTS.adapters.django import message_mutations_acceptance as cli
            def execute(report, tools, private, log):
                Path(sys.argv[2]).write_text(str(private))
                try:
                    signal.raise_signal(signal.SIGTERM)
                finally:
                    cli.begin_cleanup()
                    time.sleep(20)
            with patch.object(cli, 'preflight', return_value={}), patch.object(cli, 'execute_private', side_effect=execute):
                cli.main(['--output', sys.argv[1], '--deadline', '1', '--cleanup-grace', '0.05'])
        '''
        with tempfile.TemporaryDirectory() as directory:
            output, marker = Path(directory) / 'fresh', Path(directory) / 'private-path'
            try:
                result = subprocess.run([sys.executable, '-B', '-c', textwrap.dedent(script), str(output), str(marker)],
                                        cwd=cli.ROOT, capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 124, result)
                receipt = json.loads((output / 'results.json').read_text())
                self.assertFalse(receipt['success'])
                self.assertFalse(receipt['cleanup_verified'])
                self.assertEqual(receipt['private_evidence'], 'cleanup-unverified')
                self.assertFalse(receipt['owned_directory_removed'])
                self.assertFalse(receipt['private_directory_removed'])
                self.assertEqual(receipt['errors'], ['execution.cleanup-grace-expired'])
                self.assertEqual(result.stdout + result.stderr, b'')
            finally:
                # Only this probe's synthetic retained evidence is removed.
                if marker.exists():
                    import shutil
                    shutil.rmtree(Path(marker.read_text()))


if __name__ == '__main__':
    unittest.main()
