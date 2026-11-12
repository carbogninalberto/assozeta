"""Run the authored read-route locale matrix against an immutable Linux image.

The unchanged Django/Go/Caddy oracle runs Django first, then the supplied
packaged server and both versioned transports on the same private database.
This is current-schema HTTP evidence, not installed lifecycle or route
verification, and it owns no native repository test groups.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

EXPECTED_CASE_COUNT = 848
EXPECTED_SUPPORTED_LANGUAGES = 99
EXPECTED_SELECTION_CASES = 119
EXPECTED_MESSAGE_CASES = 610
LICENSE_PATH = '/app/licenses/framework-locales-NOTICE.md'
GO_REQUIRED_FILES = (
    'Dockerfile', 'go.mod', 'go.sum', 'cmd/server/main.go',
    'THIRD_PARTY_NOTICES.md',
)
ORACLE_SOURCE_FILES = (
    'TESTS/adapters/go/read_route_locales_image.py',
    'TESTS/adapters/go/test_read_route_locales_image.py',
    'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/payment_category_server.py',
    'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/go/impersonation_management_image.py',
    'TESTS/adapters/go/server.py',
    'TESTS/adapters/django/read_route_locales.py',
    'TESTS/adapters/django/production_runtime.py',
    'TESTS/adapters/django/settings.py',
    'TESTS/adapters/django/accounts.py',
    'TESTS/adapters/django/payment_info_stats.py',
    'TESTS/contracts/test_read_route_locales.py',
    'TESTS/fixtures/read-route-locales.json',
    'TESTS/adapters/caddy.py',
    'TESTS/http_client.py',
    'selfhost/caddy/Caddyfile',
    'UI/Dockerfile',
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def go_source_hashes(source):
    """Bind the complete Docker build context, not only Go build inputs."""
    context = source / 'BE_GO'
    assert context.is_dir(), 'Go source root must contain BE_GO'
    from TESTS.adapters.go.impersonation_management_image import go_source_hashes as shared_hashes
    hashes = shared_hashes(source)
    assert all('BE_GO/' + name in hashes for name in GO_REQUIRED_FILES), 'Incomplete Go build context'
    return hashes


def source_hashes(source):
    """Bind the Django runtime, oracle, and complete packaged build context."""
    paths = set((source / 'BE').rglob('*.py'))
    paths.update(source / name for name in ORACLE_SOURCE_FILES)
    paths.update(path for path in (source / 'BE_GO').rglob('*')
                 if path.is_file() and '.git' not in path.parts)
    return {path.relative_to(source).as_posix(): _sha256(path) for path in sorted(paths)}


def verify_build_receipt(build, source, image_id, revision):
    """Require an exact source, image, binary and revision binding."""
    assert isinstance(build, dict), 'Build receipt must be an object'
    assert build.get('success') is True, 'Build receipt is not successful'
    assert build.get('source_stable') is True, 'Build source was not stable'
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', image_id or ''), 'Image must be immutable'
    assert re.fullmatch(r'[0-9a-f]{40}', revision or ''), 'Expected revision must be full length'
    assert build.get('image_id') == image_id, 'Image differs from build receipt'
    assert build.get('source_revision') == revision, 'Receipt revision differs from expected revision'
    assert build.get('source_sha256') == go_source_hashes(source), 'Image build source differs'
    assert re.fullmatch(r'[0-9a-f]{64}', build.get('server_sha256') or ''), \
        'Build receipt must bind the packaged server binary'
    return build['server_sha256']


def license_evidence(actual, expected, path=LICENSE_PATH):
    """Reject missing, transformed or merely equivalent license payloads."""
    assert type(actual) is bytes, 'Packaged third-party license is missing'
    assert type(expected) is bytes and expected, 'Expected third-party license is missing'
    assert actual == expected, 'Packaged third-party license bytes differ'
    return {'path': path, 'exact_bytes': True, 'bytes': len(actual),
            'sha256': hashlib.sha256(actual).hexdigest()}


def verify_binary_digest(actual, expected=None, build=None):
    assert re.fullmatch(r'[0-9a-f]{64}', actual or ''), 'Invalid packaged binary digest'
    if expected is not None:
        assert actual == expected, 'Packaged binary differs from image provenance'
    if build is not None:
        assert actual == build['server_sha256'], 'Packaged binary differs from build receipt'
    return actual


def assert_oracle_workloads(result, image_id, binary_sha256):
    """Keep the native matrix's exact cardinality and evidence semantics."""
    for key in ('success', 'source_stable', 'runtime_stable', 'cleanup_verified',
                'proxy_cleanup_verified', 'fallback_rejected'):
        assert result.get(key) is True, key
    assert result.get('complete_route_parity') is False
    assert result.get('cleanup_errors') == []
    assert result.get('managed_models') == 128
    assert result.get('supported_languages') == EXPECTED_SUPPORTED_LANGUAGES
    assert result.get('selection_cases') == EXPECTED_SELECTION_CASES
    assert result.get('message_cases') == EXPECTED_MESSAGE_CASES
    assert result.get('expected_cases') == EXPECTED_CASE_COUNT
    assert verify_binary_digest(result.get('packaged_binary_sha256'), binary_sha256)
    assert len(result.get('caddy_containers', [])) == 2

    workloads = result.get('workloads', [])
    indexed = {row['backend']: row for row in workloads}
    assert len(indexed) == len(workloads) == 4
    assert set(indexed) == {'django', 'go', 'v1', 'v2'}
    django_cases = indexed['django']['cases']
    assert len(django_cases) == EXPECTED_CASE_COUNT
    names = [case['name'] for case in django_cases]
    assert len(names) == len(set(names)) == EXPECTED_CASE_COUNT
    for backend, workload in indexed.items():
        assert workload.get('success') is True
        assert workload.get('managed_state_preserved') is True
        assert workload.get('cache_preserved') is True
        assert [case['name'] for case in workload['cases']] == names
        assert all(case.get('success') is True for case in workload['cases'])
        assert all(case.get('django_requests') == int(backend in ('django', 'v1'))
                   for case in workload['cases'])

    evidence = result.get('database_write_evidence', {})
    for key in ('success', 'relation_identities_unchanged',
                'statistics_reset_unchanged', 'database_unchanged'):
        assert evidence.get(key) is True, key
    assert evidence.get('user_table_changes') == []
    assert evidence.get('user_totals_delta') == {
        'inserted': 0, 'updated': 0, 'deleted': 0}
    return result


def assert_acceptance(result, image_id, binary_sha256):
    for key in ('success', 'packaged_checks_passed', 'cleanup_verified',
                'private_directory_removed', 'source_stable', 'build_source_stable'):
        assert result.get(key) is True, key
    assert result.get('route_verification_claimed') is False
    assert result.get('cleanup_errors') == []
    assert result.get('image', {}).get('immutable_id') == image_id
    assert result.get('image', {}).get('revision') == result.get('expected_revision')
    license_row = result.get('third_party_license', {})
    assert license_row.get('path') == LICENSE_PATH
    assert license_row.get('exact_bytes') is True
    assert license_row.get('sha256') == result.get('third_party_license_source_sha256')
    assert license_row.get('bytes') == result.get('third_party_license_bytes')
    assert_oracle_workloads(result['oracle'], image_id, binary_sha256)
    return result


def _container_bytes(container, path):
    try:
        completed = subprocess.run(
            ['docker', 'exec', container, 'cat', path],
            capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Packaged license read timed out') from None
    if completed.returncode:
        raise RuntimeError('Packaged third-party license is missing')
    return completed.stdout


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
                        default=ROOT / 'quality-reports/go-migration/read-route-locales-image')
    args = parser.parse_args()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image):
        parser.error('--image must be an immutable image ID')
    if not re.fullmatch(r'[0-9a-f]{40}', args.expected_revision):
        parser.error('--expected-revision must be a full Git revision')
    args.output = args.output.resolve()
    args.go_source = args.go_source.resolve()
    args.oracle_source = args.oracle_source.resolve()
    assert args.oracle_source == ROOT, 'Oracle evidence must bind the executing checkout'
    args.output.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output / 'results.json'
    if receipt_path.exists():
        parser.error('Use a fresh output directory; receipts must not be overwritten')

    started = time.monotonic()
    build = proof = work = None
    cleanup_errors = []
    report = {
        'success': False,
        'scope': ('Current-schema packaged read-route locale HTTP; not route '
                  'verification, installed lifecycle, migration history or activation'),
        'route_verification_claimed': False,
        'complete_route_parity': False,
        'expected_revision': args.expected_revision,
        'matrix_case_count': EXPECTED_CASE_COUNT,
        'packaged_checks_passed': False,
        'cleanup_verified': False,
        'private_directory_removed': False,
        'source_stable': False,
        'build_source_stable': False,
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    receipt_path.write_text(json.dumps(report, indent=2) + '\n')
    try:
        from TESTS.adapters.django.production_runtime import require_production_python
        require_production_python()
        from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
        from TESTS.adapters.go.payment_category_image import DATABASE, ImageFixture
        from TESTS.adapters.django import read_route_locales
        from TESTS.contracts.test_read_route_locales import (
            HTTP_CASE_COUNT, SELECTION_CASE_COUNT, SUPPORTED_LANGUAGE_COUNT,
        )
        assert (SELECTION_CASE_COUNT * 2 + HTTP_CASE_COUNT) == EXPECTED_CASE_COUNT
        assert SUPPORTED_LANGUAGE_COUNT == EXPECTED_SUPPORTED_LANGUAGES
        assert SELECTION_CASE_COUNT == EXPECTED_SELECTION_CASES
        assert HTTP_CASE_COUNT == EXPECTED_MESSAGE_CASES

        report['source_sha256'] = source_hashes(args.oracle_source)
        report['build_context_sha256'] = go_source_hashes(args.go_source)
        report['third_party_license_source_sha256'] = _sha256(
            args.go_source / 'BE_GO/THIRD_PARTY_NOTICES.md')
        report['third_party_license_bytes'] = len(
            (args.go_source / 'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes())

        report['build_receipt_sha256'] = _sha256(args.build_receipt)
        build = json.loads(args.build_receipt.read_text())
        receipt_binary = verify_build_receipt(
            build, args.go_source, args.image, args.expected_revision)
        report['build_receipt'] = build
        report['build_receipt_binary_sha256'] = receipt_binary
        report['source_binding'] = {'mode': 'strict build receipt'}

        tool_env = {key: value for key, value in os.environ.items()
                    if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE',
                               'GOTOOLCHAIN')}
        tool_env.update(TESTS_READ_ROUTE_LOCALES_GO='1',
                        TESTS_READ_ROUTE_LOCALES_VERSIONED='1')
        with tempfile.TemporaryDirectory(
                prefix='az-read-route-locales-image-', dir='/tmp') as directory, \
                (args.output / 'services.log').open('w') as log:
            work = Path(directory)
            proof = ImageFixture(args.image, args.expected_revision,
                                 work, args.output, log)
            oracle_output = work / 'oracle'
            try:
                proof.start_services(args.postgres, args.redis)
                assert proof.image_id == args.image, 'Resolved image differs from supplied ID'
                assert proof.image_id == build['image_id']
                initialize_current_schema(
                    work, '127.0.0.1', proof.postgres_port, DATABASE, log)

                from django.test import override_settings
                cache = {'default': {
                    'BACKEND': 'django_redis.cache.RedisCache',
                    'LOCATION': f'redis://127.0.0.1:{proof.redis_port}/1',
                    'OPTIONS': {
                        'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                        'PASSWORD': 'synthetic-identity-only',
                    },
                }}

                @contextlib.contextmanager
                def verified_image_server(server_work, server_log, server_env,
                                          address, *, versioned=False):
                    with proof.server(server_work, server_log, server_env, address,
                                      versioned=versioned) as runtime:
                        server_binary = runtime['metadata'].get('server_sha256')
                        expected_binary = build['server_sha256']
                        verify_binary_digest(server_binary, expected_binary, build)
                        container = proof.identifier + '-server'
                        actual_license = _container_bytes(container, LICENSE_PATH)
                        expected_license = (
                            args.go_source / 'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes()
                        report['third_party_license'] = license_evidence(
                            actual_license, expected_license)
                        report['binary_sha256'] = server_binary
                        yield runtime

                with override_settings(CACHES=cache):
                    result = read_route_locales.run(
                        work, log, tool_env, f'127.0.0.1:{proof.redis_port}',
                        go_server_factory=verified_image_server,
                        output=oracle_output, database_name=DATABASE,
                        database_host_prefix='127.0.0.1')
                assert_oracle_workloads(result, proof.image_id,
                                        report['binary_sha256'])
                report['oracle'] = result
                report['packaged_checks_passed'] = True
            finally:
                try:
                    oracle_path = oracle_output / 'http-results.json'
                    if oracle_path.exists():
                        oracle = json.loads(oracle_path.read_text())
                        report.setdefault('oracle', oracle)
                        (args.output / 'http-results.json').write_text(
                            json.dumps(oracle, indent=2) + '\n')
                except BaseException as error:
                    cleanup_errors.append({
                        'stage': 'safe_oracle_receipt',
                        'error_type': type(error).__name__,
                    })
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                except BaseException as error:
                    cleanup_errors.append({
                        'stage': 'database_connections',
                        'error_type': type(error).__name__,
                    })
                try:
                    proof.cleanup()
                except BaseException as error:
                    cleanup_errors.append({
                        'stage': 'owned_containers_network',
                        'error_type': type(error).__name__,
                    })
        assert not cleanup_errors, 'Packaged fixture cleanup failed'
    except BaseException as error:
        report['error_type'] = type(error).__name__
    finally:
        try:
            report['source_stable'] = bool(
                report.get('source_sha256')
                and source_hashes(args.oracle_source) == report['source_sha256'])
        except BaseException as error:
            cleanup_errors.append({
                'stage': 'source_integrity',
                'error_type': type(error).__name__,
            })
        try:
            report['build_source_stable'] = bool(
                report.get('build_context_sha256')
                and go_source_hashes(args.go_source) == report['build_context_sha256'])
            if build is not None:
                verify_build_receipt(
                    build, args.go_source, args.image, args.expected_revision)
        except BaseException as error:
            cleanup_errors.append({
                'stage': 'build_source_integrity',
                'error_type': type(error).__name__,
            })
        report.update(
            image=proof.metadata if proof else {},
            cleanup_errors=cleanup_errors,
            cleanup_verified=bool(
                proof and proof.cleanup_verified and not cleanup_errors),
            private_directory_removed=bool(work and not work.exists()),
            elapsed_seconds=round(time.monotonic() - started, 3),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        report['success'] = bool(
            report.get('packaged_checks_passed')
            and report['cleanup_verified']
            and report['private_directory_removed']
            and report['source_stable']
            and report['build_source_stable']
            and 'error_type' not in report
        )
        receipt_path.write_text(json.dumps(report, indent=2) + '\n')
    if not report['success']:
        print('Packaged read-route locale checks failed; see typed results.json receipt.',
              file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({
        'success': True,
        'image': proof.image_id,
        'binary_sha256': report['binary_sha256'],
        'matrix_cases_per_workload': EXPECTED_CASE_COUNT,
        'route_verification_claimed': False,
        'cleanup_verified': True,
    }))


if __name__ == '__main__':
    main()
