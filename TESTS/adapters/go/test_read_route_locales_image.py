"""Portable negative controls for packaged read-route locale acceptance."""
import copy
import hashlib
import inspect
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from TESTS.adapters.django.read_route_locales import OUTPUT, run
from TESTS.adapters.go.read_route_locales_image import (
    EXPECTED_CASE_COUNT,
    EXPECTED_MESSAGE_CASES,
    EXPECTED_SELECTION_CASES,
    EXPECTED_SUPPORTED_LANGUAGES,
    LICENSE_PATH,
    assert_acceptance,
    go_source_hashes,
    license_evidence,
    verify_binary_digest,
    verify_build_receipt,
)
from TESTS.contracts.test_read_route_locales import (
    HTTP_CASE_COUNT,
    SELECTION_CASE_COUNT,
    SUPPORTED_LANGUAGE_COUNT,
)


IMAGE = 'sha256:' + 'a' * 64
BINARY = 'b' * 64
REVISION = 'c' * 40


class ReadRouteLocaleImageControlTests(unittest.TestCase):
    def test_runner_keeps_its_native_interface_and_defaults(self):
        parameters = list(inspect.signature(run).parameters.values())
        self.assertEqual([item.name for item in parameters[:4]],
                         ['work', 'log', 'tool_env', 'address'])
        defaults = run.__kwdefaults__
        self.assertIs(defaults['go_server_factory'], None)
        self.assertEqual(defaults['output'], OUTPUT)
        self.assertEqual(defaults['database_name'], 'assozeta_contract_accounts')
        self.assertEqual(defaults['database_host_prefix'], '/tmp/az-accounts-')
        self.assertEqual(SELECTION_CASE_COUNT * 2 + HTTP_CASE_COUNT,
                         EXPECTED_CASE_COUNT)
        self.assertEqual((SUPPORTED_LANGUAGE_COUNT, SELECTION_CASE_COUNT,
                          HTTP_CASE_COUNT),
                         (EXPECTED_SUPPORTED_LANGUAGES, EXPECTED_SELECTION_CASES,
                          EXPECTED_MESSAGE_CASES))

    def test_build_receipt_binds_complete_context_image_revision_and_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            context = source / 'BE_GO'
            files = {
                'Dockerfile': b'FROM fixture\n',
                'go.mod': b'module private.test/image\n',
                'go.sum': b'',
                'cmd/server/main.go': b'package main\n',
                'THIRD_PARTY_NOTICES.md': b'private fixture notice\n',
                'testdata/extra.txt': b'part of the complete build context\n',
            }
            for name, contents in files.items():
                path = context / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            build = {
                'success': True,
                'source_stable': True,
                'source_revision': REVISION,
                'image_id': IMAGE,
                'source_sha256': go_source_hashes(source),
                'server_sha256': BINARY,
            }
            self.assertEqual(
                verify_build_receipt(build, source, IMAGE, REVISION), BINARY)

            for key, value in (
                    ('success', False), ('success', 1), ('source_stable', False),
                    ('source_revision', 'd' * 40), ('image_id', 'sha256:' + 'd' * 64),
                    ('source_sha256', {}), ('server_sha256', ''),
                    ('server_sha256', 'not-a-digest')):
                with self.subTest(key=key, value=value), self.assertRaises(AssertionError):
                    altered = copy.deepcopy(build)
                    altered[key] = value
                    verify_build_receipt(altered, source, IMAGE, REVISION)

            (context / 'testdata/extra.txt').write_bytes(b'changed context\n')
            with self.assertRaises(AssertionError):
                verify_build_receipt(build, source, IMAGE, REVISION)
            (context / 'testdata/extra.txt').write_bytes(files['testdata/extra.txt'])
            (context / 'testdata/added.txt').write_bytes(b'new file\n')
            with self.assertRaises(AssertionError):
                verify_build_receipt(build, source, IMAGE, REVISION)

    def test_frozen_build_context_beneath_reports_is_not_silently_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'quality-reports' / 'frozen-source'
            context = source / 'BE_GO'
            for name in ('Dockerfile', 'go.mod', 'go.sum', 'cmd/server/main.go',
                         'THIRD_PARTY_NOTICES.md'):
                path = context / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('authored input')
            generated = context / 'quality-reports' / 'temporary.log'
            generated.parent.mkdir()
            generated.write_text('not a build input')
            hashes = go_source_hashes(source)
            self.assertEqual(len(hashes), 5)
            self.assertIn('BE_GO/cmd/server/main.go', hashes)
            (context / 'cmd/server/main.go').write_text('changed source')
            self.assertNotEqual(go_source_hashes(source), hashes)

    def test_license_requires_exact_bytes_not_equivalent_text(self):
        expected = b'Third-party notices\nline two\n'
        evidence = license_evidence(expected, expected)
        self.assertEqual(evidence, {
            'path': LICENSE_PATH,
            'exact_bytes': True,
            'bytes': len(expected),
            'sha256': hashlib.sha256(expected).hexdigest(),
        })
        for actual in (expected.replace(b'\n', b'\r\n'), expected[:-1],
                       expected + b'\n', b'', None):
            with self.subTest(actual=actual), self.assertRaises(AssertionError):
                license_evidence(actual, expected)

    def test_binary_digest_rejects_metadata_or_receipt_substitution(self):
        build = {'server_sha256': BINARY}
        self.assertEqual(verify_binary_digest(BINARY, BINARY, build), BINARY)
        for actual, expected in ((BINARY, 'd' * 64), ('short', 'short'),
                                 ('d' * 64, 'd' * 64)):
            with self.subTest(actual=actual, expected=expected), \
                    self.assertRaises(AssertionError):
                verify_binary_digest(actual, expected, build)

    def test_omitted_receipt_is_rejected_before_creating_fixture_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'unstarted-acceptance'
            adapter = Path(__file__).with_name('read_route_locales_image.py')
            result = subprocess.run([
                sys.executable, str(adapter), '--image', IMAGE,
                '--expected-revision', REVISION, '--output', str(output),
            ], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('--build-receipt', result.stderr)
            self.assertIn('required', result.stderr)
            self.assertFalse(output.exists())

    def acceptance(self):
        names = [f'case-{index:03d}' for index in range(EXPECTED_CASE_COUNT)]
        workloads = []
        for backend in ('django', 'go', 'v1', 'v2'):
            workloads.append({
                'backend': backend,
                'success': True,
                'managed_state_preserved': True,
                'cache_preserved': True,
                'cases': [{
                    'name': name,
                    'success': True,
                    'status': 200,
                    'django_requests': int(backend in ('django', 'v1')),
                } for name in names],
            })
        oracle = {
            'success': True,
            'source_stable': True,
            'runtime_stable': True,
            'cleanup_verified': True,
            'proxy_cleanup_verified': True,
            'fallback_rejected': True,
            'complete_route_parity': False,
            'cleanup_errors': [],
            'managed_models': 128,
            'supported_languages': EXPECTED_SUPPORTED_LANGUAGES,
            'selection_cases': EXPECTED_SELECTION_CASES,
            'message_cases': EXPECTED_MESSAGE_CASES,
            'expected_cases': EXPECTED_CASE_COUNT,
            'packaged_binary_sha256': BINARY,
            'caddy_containers': [{'container_id': 'one'}, {'container_id': 'two'}],
            'workloads': workloads,
            'database_write_evidence': {
                'success': True,
                'relation_identities_unchanged': True,
                'statistics_reset_unchanged': True,
                'database_unchanged': True,
                'user_table_changes': [],
                'user_totals_delta': {
                    'inserted': 0, 'updated': 0, 'deleted': 0},
            },
        }
        license_row = license_evidence(b'notice\n', b'notice\n')
        return {
            'success': True,
            'packaged_checks_passed': True,
            'cleanup_verified': True,
            'private_directory_removed': True,
            'source_stable': True,
            'build_source_stable': True,
            'route_verification_claimed': False,
            'cleanup_errors': [],
            'expected_revision': REVISION,
            'image': {'immutable_id': IMAGE, 'revision': REVISION},
            'third_party_license': license_row,
            'third_party_license_source_sha256': license_row['sha256'],
            'third_party_license_bytes': license_row['bytes'],
            'oracle': oracle,
        }

    def test_complete_acceptance_is_accepted(self):
        result = self.acceptance()
        self.assertIs(assert_acceptance(result, IMAGE, BINARY), result)

    def test_rejects_shortcuts_mutations_attribution_and_cleanup_failures(self):
        original = self.acceptance()
        mutations = (
            lambda result: result.update(success=False),
            lambda result: result.update(packaged_checks_passed=False),
            lambda result: result.update(cleanup_verified=False),
            lambda result: result.update(private_directory_removed=False),
            lambda result: result.update(source_stable=False),
            lambda result: result.update(build_source_stable=False),
            lambda result: result.update(route_verification_claimed=True),
            lambda result: result['oracle'].update(success=False),
            lambda result: result['oracle'].update(runtime_stable=False),
            lambda result: result['oracle'].update(cleanup_verified=False),
            lambda result: result['oracle'].update(proxy_cleanup_verified=False),
            lambda result: result['oracle'].update(fallback_rejected=False),
            lambda result: result['oracle'].update(complete_route_parity=True),
            lambda result: result['oracle'].update(managed_models=127),
            lambda result: result['oracle'].update(expected_cases=847),
            lambda result: result['oracle'].update(caddy_containers=[]),
            lambda result: result['oracle']['workloads'].pop(),
            lambda result: result['oracle']['workloads'][1]['cases'].pop(),
            lambda result: result['oracle']['workloads'][0]['cases'][0].update(
                success=False),
            lambda result: result['oracle']['workloads'][1]['cases'][0].update(
                django_requests=1),
            lambda result: result['oracle']['workloads'][2]['cases'][0].update(
                django_requests=0),
            lambda result: result['oracle']['workloads'][0].update(
                managed_state_preserved=False),
            lambda result: result['oracle']['workloads'][1].update(
                cache_preserved=False),
            lambda result: result['oracle']['workloads'][3]['cases'][0].update(
                name='replacement'),
            lambda result: result['oracle']['database_write_evidence'].update(
                success=False),
            lambda result: result['oracle']['database_write_evidence'][
                'user_totals_delta'].update(updated=1),
            lambda result: result['oracle']['database_write_evidence'].update(
                user_table_changes=[{'oid': '1'}]),
            lambda result: result['oracle'].update(
                packaged_binary_sha256='d' * 64),
            lambda result: result['third_party_license'].update(exact_bytes=False),
            lambda result: result['third_party_license'].update(sha256='d' * 64),
            lambda result: result['image'].update(immutable_id='sha256:' + 'd' * 64),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                result = copy.deepcopy(original)
                mutate(result)
                with self.assertRaises(AssertionError):
                    assert_acceptance(result, IMAGE, BINARY)


if __name__ == '__main__':
    unittest.main()
