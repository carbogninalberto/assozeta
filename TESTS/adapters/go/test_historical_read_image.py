"""Offline controls; these do not establish packaged historical acceptance."""
import copy
from contextlib import contextmanager, redirect_stderr
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from TESTS.adapters.go import historical_read_image as image

IMAGE = 'sha256:' + 'a' * 64
REVISION = 'b' * 40
BINARY = 'c' * 64
BOOT = '11111111-1111-4111-8111-111111111111'


def cache():
    return {'keys_compared': 2, 'contrast_keys_verified': 2, 'typed_values_exact': True,
            'absolute_expirations_exact': True, 'readiness_prefix_excluded': False,
            'cache_sha256': 'e' * 64}


def acceptance(first='django'):
    from TESTS.fixtures.historical_read_routes import load_contract
    manifest = load_contract()['http']['manifest']
    caddy = image.re.search(r'^FROM (caddy:\S+) AS production$',
                           (image.ROOT / 'UI/Dockerfile').read_text(), image.re.MULTILINE).group(1)
    misroute = 'historical-caddy-v2-misrouted-to-django'
    specifications = image.oracle.expected_workloads(first, True)
    workloads, logins, samples = [], [], []
    at = datetime(2026, 10, 25, tzinfo=timezone.utc)
    for index, spec in enumerate(specifications):
        workloads.append({**spec, 'success': True, 'tests': 4, 'logins': 1,
                          'django_requests': 8 if spec['backend'] == 'django' else 0,
                          'reads': [{'unchanged': True, 'managed_models': 128,
                                     'cache': cache(), 'cache_preflight': cache()} for _ in range(7)]})
        login = {'workload': spec['label'], 'backend': spec['backend'], 'exact_projection': True,
                 'cache': cache(), 'password_upgrade': {'performed': index == 0},
                 'password_hash_preserved': index != 0, 'last_login': at.isoformat()}
        logins.append(login)
        if spec['backend'] == 'go':
            cid = 'historical-' + spec['label'] + '-' + str(index)
            samples.extend([{'boundary': cid + '.before', 'timestamp': at.isoformat()},
                            {'boundary': cid + '.after', 'timestamp': (at + timedelta(seconds=1)).isoformat()}])
        at += timedelta(seconds=2)
    oracle = {key: True for key in ('runtime_checks_passed', 'source_stable', 'cleanup_verified',
                                   'go_server_cleanup_verified', 'proxy_cleanup_verified',
                                   'final_login_projection_verified', 'import_worker_quiescent_before_baseline')}
    oracle.update(first_backend=first, versioned=True, go_import_supported=False,
                  whole_read_family_verified=False, historical_population={'exact': True},
                  schema={'mode': 'full-migration-history', 'remaining_plan': 0, 'applied_migrations': 548},
                  **{'import': {'success': True, 'skipped_records': image.oracle.SKIPS, 'warnings': []}},
                  configuration={'success': True}, workloads=workloads, logins=logins,
                  workload_counts=image.oracle.assert_workload_receipts(workloads, specifications),
                  first_go_login_initialization_exercised=first == 'go', final_cache=cache(),
                  go_direct_request_headers={'X-Forwarded-For': '127.0.0.1'},
                  go_runtime={'kind': 'immutable-production-image', 'immutable_id': IMAGE,
                              'server_sha256': BINARY, 'uid': 10001, 'user': '10001:10001',
                              'mounted_executables': False, 'entrypoint': ['/app/server'],
                              **{key: True for key in ('same_imported_database', 'readonly_root',
                                 'health_version_matches_package', 'kernel_clock_verified', 'notice_exact_bytes')}},
                  go_login_clock={'source': {'kind': 'postgresql_clock_timestamp', 'query': image.CLOCK_QUERY,
                      'database': image.DATABASE, 'clock_domain': image.CLOCK_DOMAIN, 'kernel_boot_id': BOOT},
                      'samples': samples},
                  versioned_runtime={'routes': {'/api/v1': 'django', '/api/v2': 'go'},
                      'same_imported_database': True, 'proxy_cleanup_verified': True,
                      'container_id': '1' * 64, 'image_id': IMAGE, 'caddy_image': caddy,
                      'attribution': {'v1_expected_django_requests': 8, 'v1_observed_django_requests': 8,
                                      'v2_expected_django_requests': 0, 'v2_observed_django_requests': 0},
                      'misroute_negative_control': {'rejected': True, 'equivalent_response_verified': True,
                          'state_and_cache_preserved_before_rejection': True, 'container_id': '2' * 64,
                          'image_id': IMAGE, 'caddy_image': caddy, 'label': misroute, 'status': manifest['status'],
                          'expected_backend': 'go', 'observed_backend': 'django',
                          'django_requests': [{'cid': misroute, 'method': 'GET', 'path': manifest['path'],
                                               'status': manifest['status']}]}})
    return {'success': True, 'packaged_checks_passed': True, 'cleanup_verified': True,
            'private_directory_removed': True, 'source_stable': True, 'build_source_stable': True,
            'build_receipt_stable': True, 'cleanup_errors': [], 'both_orders_verified': False,
            'packaged_django_verified': False, 'image_id': IMAGE, 'expected_revision': REVISION,
            'binary_sha256': BINARY, 'first_backend': first, 'private_fixture_id': 'private-' + first,
            'source_sha256': {'input': BINARY}, 'build_context_sha256': {'BE_GO/server': BINARY},
            'build_receipt': {'success': True, 'source_stable': True, 'image_id': IMAGE,
                              'source_revision': REVISION, 'server_sha256': BINARY,
                              'source_sha256': {'BE_GO/server': BINARY}},
            'build_receipt_sha256': BINARY, 'image': {'immutable_id': IMAGE, 'revision': REVISION},
            'third_party_license': image.license_evidence(b'notice\n', b'notice\n'),
            'third_party_license_source_sha256': hashlib.sha256(b'notice\n').hexdigest(),
            'third_party_license_bytes': 7, 'oracle': oracle}


class HistoricalImageControls(unittest.TestCase):
    def test_exact_build_context_receipt_rejects_image_source_revision_and_binary_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'quality-reports/frozen'
            for name in image.GO_REQUIRED_FILES:
                path = source / 'BE_GO' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            embedded = source / 'BE_GO/assets/embedded.json'
            embedded.parent.mkdir()
            embedded.write_text('authored bytes')
            build = {'success': True, 'source_stable': True, 'image_id': IMAGE,
                     'source_revision': REVISION, 'server_sha256': BINARY,
                     'source_sha256': image.go_source_hashes(source)}
            self.assertIn('BE_GO/assets/embedded.json', build['source_sha256'])
            self.assertEqual(image.verify_build_receipt(build, source, IMAGE, REVISION), BINARY)
            for key, value in [('success', False), ('source_stable', False), ('image_id', 'tag:latest'),
                               ('source_revision', REVISION[:8]), ('source_sha256', {}),
                               ('server_sha256', ''), ('server_sha256', 'bogus')]:
                with self.subTest(key=key, value=value), self.assertRaises(AssertionError):
                    image.verify_build_receipt(dict(build, **{key: value}), source, IMAGE, REVISION)
            for requested, revision in [('tag:latest', REVISION), (IMAGE, REVISION[:8]),
                                        ('sha256:' + 'd' * 64, REVISION), (IMAGE, 'd' * 40)]:
                with self.assertRaises(AssertionError):
                    image.verify_build_receipt(build, source, requested, revision)
            embedded.write_text('substituted bytes')
            with self.assertRaisesRegex(AssertionError, 'source'):
                image.verify_build_receipt(build, source, IMAGE, REVISION)

    def test_executing_oracle_must_equal_frozen_image_source(self):
        with patch.object(image, 'go_source_hashes', side_effect=[{'a': BINARY}, {'a': 'd' * 64}]):
            with self.assertRaisesRegex(AssertionError, 'executing'):
                image.verify_oracle_source(Path('/frozen'))

    def test_missing_receipt_or_nonimmutable_cli_fails_before_fixture_creation(self):
        for args in [[], ['--image', 'tag:latest', '--expected-revision', REVISION,
                          '--build-receipt', '/missing', '--first-backend', 'go', '--output', '/unused'],
                     ['--image', IMAGE, '--expected-revision', REVISION[:8],
                      '--build-receipt', '/missing', '--first-backend', 'go', '--output', '/unused']]:
            with self.subTest(args=args), patch.object(image, 'run_image') as run, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    image.main(args)
                run.assert_not_called()

    def test_binary_and_notice_require_exact_bytes(self):
        self.assertEqual(image.verify_binary_digest(BINARY, BINARY), BINARY)
        with self.assertRaises(AssertionError):
            image.verify_binary_digest('d' * 64, BINARY)
        for actual in (None, b'notice\r\n', b'changed\n'):
            with self.assertRaises(AssertionError):
                image.license_evidence(actual, b'notice\n')

    def test_both_orders_require_two_separate_successful_fixtures_and_same_bound_image(self):
        reports = [acceptance('django'), acceptance('go')]
        self.assertTrue(image.assert_both_orders(reports)['both_orders_verified'])
        for fault in ('missing', 'duplicate_order', 'same_fixture', 'different_binary', 'different_source'):
            altered = copy.deepcopy(reports)
            if fault == 'missing':
                altered.pop()
            elif fault == 'duplicate_order':
                altered[1] = copy.deepcopy(altered[0])
            elif fault == 'same_fixture':
                altered[1]['private_fixture_id'] = altered[0]['private_fixture_id']
            elif fault == 'different_binary':
                altered[1]['binary_sha256'] = 'd' * 64
            else:
                altered[1]['source_sha256'] = {}
            with self.subTest(fault=fault), self.assertRaises(AssertionError):
                image.assert_both_orders(altered)

    def test_each_order_requires_exact_pinned_548_migration_history(self):
        for first in ('django', 'go'):
            self.assertIs(image.assert_acceptance(acceptance(first))['success'], True)
            for count in (1, 547, 549, 0, -1, True, 548.0, '548', None):
                report = acceptance(first)
                report['oracle']['schema']['applied_migrations'] = count
                with self.subTest(first=first, count=count), self.assertRaises(AssertionError):
                    image.assert_acceptance(report)
            report = acceptance(first)
            del report['oracle']['schema']['applied_migrations']
            with self.subTest(first=first, count='missing'), self.assertRaises(AssertionError):
                image.assert_acceptance(report)

    def test_both_orders_reject_inconsistent_or_equally_unpinned_migration_histories(self):
        for counts in ((1, 547), (547, 1), (548, 547), (547, 548),
                       (548, 549), (549, 548), (547, 547), (549, 549)):
            reports = [acceptance('django'), acceptance('go')]
            for report, count in zip(reports, counts):
                report['oracle']['schema']['applied_migrations'] = count
            with self.subTest(counts=counts), self.assertRaises(AssertionError):
                image.assert_both_orders(reports)

    def test_rejects_native_receipts_shortcuts_population_cache_audit_attribution_and_cleanup_gaps(self):
        faults = [
            lambda r: r.update(cleanup_verified=False),
            lambda r: r.update(private_directory_removed=False),
            lambda r: r.update(source_stable=False),
            lambda r: r.update(build_source_stable=False),
            lambda r: r.update(build_receipt_stable=False),
            lambda r: r['build_receipt'].update(server_sha256='d' * 64),
            lambda r: r['build_receipt'].pop('server_sha256'),
            lambda r: r['build_receipt'].update(source_sha256={}),
            lambda r: r.update(cleanup_errors=['failed']),
            lambda r: r['image'].update(immutable_id='tag:latest'),
            lambda r: r['image'].update(revision='other'),
            lambda r: r['third_party_license'].update(sha256='d' * 64),
            lambda r: r['oracle']['go_runtime'].update(kind='native-built-server'),
            lambda r: r['oracle']['go_runtime'].update(uid=0),
            lambda r: r['oracle']['go_runtime'].update(server_sha256='d' * 64),
            lambda r: r['oracle']['go_runtime'].update(mounted_executables=True),
            lambda r: r['oracle'].update(versioned=False),
            lambda r: r['oracle']['schema'].update(mode='current-schema'),
            lambda r: r['oracle']['historical_population'].update(exact=False),
            lambda r: r['oracle'].update(go_server_cleanup_verified=False),
            lambda r: r['oracle']['versioned_runtime'].update(proxy_cleanup_verified=False),
            lambda r: r['oracle']['versioned_runtime'].update(caddy_image='caddy:wrong'),
            lambda r: r['oracle']['versioned_runtime']['misroute_negative_control'].update(rejected=False),
            lambda r: r['oracle']['versioned_runtime']['misroute_negative_control'].update(container_id='1' * 64),
            lambda r: r['oracle']['versioned_runtime']['misroute_negative_control']['django_requests'].append(
                {'cid': 'different', 'method': 'GET', 'path': '/manifest', 'status': 200}),
            lambda r: r['oracle']['workloads'][0]['reads'][0]['cache'].update(absolute_expirations_exact=False),
            lambda r: r['oracle']['workloads'][1].update(django_requests=1),
            lambda r: r['oracle']['logins'][0].update(exact_projection=False),
            lambda r: r['oracle']['logins'][0]['password_upgrade'].update(performed=False),
            lambda r: r['oracle']['workloads'].reverse(),
            lambda r: r['oracle']['workloads'].pop(),
            lambda r: r['oracle']['go_login_clock']['source'].update(clock_domain='host'),
            lambda r: r['oracle']['go_login_clock']['source'].update(kernel_boot_id=''),
            lambda r: r['oracle']['go_login_clock']['samples'].pop(),
            lambda r: r['oracle']['go_login_clock']['samples'][0].update(timestamp='2030-01-01T00:00:00+00:00'),
        ]
        for index, mutate in enumerate(faults):
            report = acceptance()
            mutate(report)
            with self.subTest(fault=index), self.assertRaises(AssertionError):
                image.assert_acceptance(report)

    def test_packaged_clock_uses_actual_database_wall_time_and_kernel_provenance(self):
        proof = image.HistoricalImageFixture(IMAGE, REVISION, Path('/unused'), Path('/unused'), io.StringIO())
        proof.postgres = 'owned-postgres'
        with patch.object(proof, 'docker', return_value=SimpleNamespace(stdout=BOOT)) as docker:
            source = proof.audit_clock_source()
        self.assertEqual(source['clock_domain'], image.CLOCK_DOMAIN)
        docker.assert_called_once_with('exec', 'owned-postgres', 'cat', '/proc/sys/kernel/random/boot_id')

    def test_actual_runtime_verifier_rejects_image_binary_revision_notice_kernel_root_and_mount_drift(self):
        proof = image.HistoricalImageFixture(IMAGE, REVISION, Path('/unused'), Path('/unused'), io.StringIO())
        proof.kernel = BOOT
        info = {'Config': {'Labels': {image.LABEL: proof.identifier}, 'User': '10001:10001',
                           'Entrypoint': ['/app/server']}, 'State': {'Running': True}, 'Image': IMAGE,
                'HostConfig': {'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                               'SecurityOpt': ['no-new-privileges'], 'Tmpfs': {'/tmp': 'rw'}},
                'Mounts': [{'Destination': '/operations', 'Type': 'bind'}]}
        for fault in (None, 'image', 'binary', 'revision', 'notice', 'kernel', 'root', 'mount', 'identity'):
            observed = copy.deepcopy(info)
            if fault == 'image':
                observed['Image'] = 'sha256:' + 'd' * 64
            elif fault == 'root':
                observed['HostConfig']['ReadonlyRootfs'] = False
            elif fault == 'mount':
                observed['Mounts'].append({'Destination': '/app/server', 'Type': 'bind'})
            elif fault == 'identity':
                observed['Config']['User'] = '0:0'

            def docker(*args, **kwargs):
                self.assertEqual(args[:2], ('exec', 'owned-server'))
                value = {'-u': '10001', '/app/CODE_REVISION': REVISION,
                         '/app/server': BINARY, '/proc/sys/kernel/random/boot_id': BOOT}[args[-1]]
                if (fault, args[-1]) in (('binary', '/app/server'), ('revision', '/app/CODE_REVISION'),
                                        ('kernel', '/proc/sys/kernel/random/boot_id')):
                    value = 'd' * 64
                return SimpleNamespace(stdout=value)

            with self.subTest(fault=fault), patch.object(proof, 'inspect', return_value=observed), \
                    patch.object(proof, 'docker', side_effect=docker), \
                    patch.object(image, '_container_bytes', return_value=b'wrong' if fault == 'notice' else b'notice\n'):
                if fault is None:
                    proof.verify_server('owned-server', BINARY, REVISION, b'notice\n')
                    self.assertEqual(proof.notice_evidence['sha256'], hashlib.sha256(b'notice\n').hexdigest())
                else:
                    with self.assertRaises(AssertionError):
                        proof.verify_server('owned-server', BINARY, REVISION, b'notice\n')

    def test_runner_forwards_unchanged_oracle_and_cannot_succeed_after_runtime_cleanup_or_input_failure(self):
        # Scripted orchestration only: never a packaged runtime acceptance claim.
        from TESTS.adapters.go import age_worker_fixture
        for fault in (None, 'runtime', 'cleanup', 'source', 'receipt', 'migrations'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                context = {'BE_GO/authored': BINARY}
                build = {'success': True, 'source_stable': True, 'image_id': IMAGE,
                         'source_revision': REVISION, 'server_sha256': BINARY, 'source_sha256': context}
                build_path = root / 'build.json'
                build_path.write_text(json.dumps(build))
                args = SimpleNamespace(output=root / 'output', go_source=image.ROOT,
                                       build_receipt=build_path, image=IMAGE, expected_revision=REVISION,
                                       first_backend='go', postgres='private-postgres', redis='private-redis')
                events = []

                class Fixture:
                    def __init__(self, requested, revision, work, output, log):
                        self.metadata = {'immutable_id': requested, 'revision': revision}
                        self.identifier = 'scripted-fixture'
                        self.work = work
                        self.cleanup_verified = False

                    def start_services(self, postgres, redis):
                        events.append('start')
                        self.image_id, self.postgres_port, self.redis_port = IMAGE, 54321, 54322

                    def audit_clock_source(self):
                        return {'kind': 'scripted-offline-clock'}

                    def read_audit_time(self):
                        raise AssertionError('No runtime clock reads in this scripted control')

                    def verified_server(self, *args, **kwargs):
                        raise AssertionError('No server launch in this scripted control')

                    def cleanup(self):
                        events.append('cleanup')
                        if fault == 'cleanup':
                            raise RuntimeError('scripted cleanup failure')
                        self.cleanup_verified = True

                def run(work, redis_port, log, report, tool_env, archive, contract, first, **kwargs):
                    events.append('oracle')
                    self.assertEqual((redis_port, first), (54322, 'go'))
                    self.assertTrue(kwargs['versioned'])
                    self.assertIs(kwargs['go_server_factory'].__func__, Fixture.verified_server)
                    self.assertEqual(kwargs['database'], {'NAME': image.DATABASE, 'HOST': '127.0.0.1',
                                                         'PORT': '54321', 'USER': 'contracts',
                                                         'PASSWORD': 'synthetic-only'})
                    self.assertEqual(kwargs['go_login_clock'].evidence['source']['kind'], 'scripted-offline-clock')
                    if fault == 'runtime':
                        raise RuntimeError('scripted oracle failure')
                    proof = kwargs['go_server_factory'].__self__
                    proof.notice_evidence = image.license_evidence(proof.notice, proof.notice)
                    report.update(acceptance('go')['oracle'])
                    report['service_cleanup'] = {name: 'stopped' for name in image.oracle.SERVICES}
                    if fault == 'migrations':
                        report['schema']['applied_migrations'] = 547
                    if fault == 'receipt':
                        build_path.write_text('{}')

                snapshots = [{'bound': BINARY}, {'bound': BINARY},
                             {'bound': 'changed' if fault == 'source' else BINARY}]
                with patch.object(image, 'HistoricalImageFixture', Fixture), \
                        patch.object(image, 'source_hashes', side_effect=snapshots), \
                        patch.object(image, 'go_source_hashes', return_value=context), \
                        patch.object(image.oracle, 'run_runtime', side_effect=run), \
                        patch.object(age_worker_fixture, 'initialize_current_schema') as initializer, \
                        patch.object(image.ImageFixture, 'docker', side_effect=AssertionError('No Docker in offline control')):
                    result = image.run_image(args)
                initializer.assert_not_called()
                self.assertEqual(events, ['start', 'oracle', 'cleanup'])
                self.assertEqual(result['success'], fault is None)
                self.assertTrue(result['private_directory_removed'])
                self.assertEqual(json.loads((args.output / 'results.json').read_text())['success'], fault is None)

    def test_packaged_teardown_verifies_ownership_removal_and_detects_failed_or_remaining_resources(self):
        for fault in (None, 'ownership', 'remove', 'remaining', 'workload', 'logs'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                proof = image.HistoricalImageFixture(IMAGE, REVISION, Path(directory), Path(directory), io.StringIO())
                proof.binary, proof.notice = BINARY, b'notice\n'
                proof.kernel = BOOT
                calls, cleanup = [], None

                @contextmanager
                def server(*args, **kwargs):
                    yield {'base_url': 'http://127.0.0.1:1', 'proxy_upstream': 'api:8000',
                           'network': 'owned', 'metadata': {'entrypoint': ['/app/server']}}

                def docker(*args, **kwargs):
                    calls.append(args)
                    if args[0] == 'inspect':
                        labels = {image.LABEL: 'foreign' if fault == 'ownership' else proof.identifier}
                        return SimpleNamespace(returncode=0, stdout=json.dumps([{'Config': {'Labels': labels}}]))
                    if args[0] == 'rm' and fault == 'remove':
                        raise RuntimeError('removal failed')
                    if args[0] == 'logs' and fault == 'logs':
                        raise RuntimeError('log collection failed')
                    return SimpleNamespace(returncode=0, stdout='remaining' if args[0] == 'ps' and fault == 'remaining' else '', stderr='')

                def exercise():
                    nonlocal cleanup
                    with proof.verified_server(Path(directory), None, {}, 'unused', versioned=True) as runtime:
                        cleanup = runtime['cleanup']
                        if fault == 'workload':
                            raise ValueError('workload failed')

                with patch.object(proof, 'server', server), patch.object(proof, 'verify_server'), \
                        patch.object(proof, 'docker', side_effect=docker):
                    if fault is None:
                        exercise()
                    else:
                        with self.assertRaises((AssertionError, RuntimeError, ValueError)):
                            exercise()
                self.assertEqual(cleanup['verified'], fault in (None, 'workload'))
                if fault == 'ownership':
                    self.assertFalse(any(call[0] == 'rm' for call in calls))
                else:
                    self.assertTrue(any(call[0] == 'rm' for call in calls))


if __name__ == '__main__':
    unittest.main()
