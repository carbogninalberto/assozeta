"""Replay the authored impersonation oracle against an immutable Linux image.

Uses the ordinary private PostgreSQL/Redis image fixture and the unchanged
management oracle, including real command-denial faults, cross-runtime sessions
and shipped Caddy routing. This proves current-schema packaged behavior only;
installation, historical data, concurrency and activation remain separate.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.payment_category_image import DATABASE, LABEL, ImageFixture

SOURCES = (
    'TESTS/adapters/go/impersonation_management_image.py',
    'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/django/impersonation_management.py',
    'TESTS/adapters/django/accounts.py',
    'TESTS/adapters/django/settings.py',
    'TESTS/contracts/test_impersonation_management.py',
    'TESTS/adapters/redis_faults.py',
    'TESTS/adapters/caddy.py', 'TESTS/http_client.py',
    'selfhost/caddy/Caddyfile', 'UI/Dockerfile',
)


def source_hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}


def go_source_hashes(source):
    """Bind the full owned build context; usable by the CI build receipt step."""
    return {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((source / 'BE_GO').rglob('*'))
              if path.is_file() and not any(part in path.relative_to(source).parts
                                          for part in ('quality-reports', '.git'))}


def build_source_matches(source, manifest):
    return bool(manifest) and go_source_hashes(source) == manifest


def assert_workloads(result, image_id):
    assert result['success'] and result['source_stable']
    assert result['managed_state_preserved'] and result['cache_preserved']
    assert result['managed_models'] == 128
    assert result['cleanup'] == {'django': True, 'go': True, 'proxy': True}
    assert result['versioned'] is True and result['cross_runtime_verified'] is True
    assert result['repository_checked'] is False and result['repository_tests'] == []
    assert result['go_runtime']['immutable_id'] == image_id
    rows = result['workloads']
    indexed = {row['backend']: row for row in rows}
    full = {'django', 'go', 'v1', 'v2'}
    rounds = {direction + '-' + actor for direction in ('django-to-go', 'go-to-django')
              for actor in ('owner', 'admin')}
    assert len(indexed) == len(rows) and set(indexed) == full | rounds | {'misrouted-v2'}
    baseline = indexed['django']
    names = [row['name'] for row in baseline['case_results']]
    assert len(names) == len(set(names)) == baseline['cases'] and baseline['cases'] == 264
    assert baseline['audits'] == 64
    for label in full:
        row = indexed[label]
        assert row['cases'] == baseline['cases'] and row['audits'] == baseline['audits']
        assert [case['name'] for case in row['case_results']] == names
        shortened = {case['name']: case['shortened_ttl'] for case in row['case_results']
                     if case.get('shortened_ttl')}
        assert set(shortened) == {'owner0-get', 'owner1-get', 'admin0-get', 'admin1-get', 'admin2-get'}
        assert all(type(ttl['before_ms']) is int and type(ttl['after_ms']) is int
                   and 0 < ttl['after_ms'] <= ttl['before_ms'] <= 60000 for ttl in shortened.values())
        faults = [case['redis_fault'] for case in row['case_results'] if case.get('redis_fault')]
        assert {fault['command'] for fault in faults} == {'GET', 'SET', 'DEL'}
        assert all(fault['fault_installed'] and fault['observed_denials'] > 0
                   and fault['acl_restored'] for fault in faults)
        assert all(fault['container_fixture']['ownership_verified']
                   and fault['container_fixture']['ephemeral_loopback_port']
                   and fault['container_fixture']['data_tmpfs'] for fault in faults)
        assert all(case['managed_state_preserved'] and case['unrelated_cache_preserved']
                   for case in row['case_results'])
        assert all(len(case['django_requests']) == int(label in ('django', 'v1'))
                   for case in row['case_results'])
    for label in rounds:
        assert indexed[label]['cases'] == 4 and indexed[label]['audits'] == 2
        shortened = [case for case in indexed[label]['case_results'] if case.get('shortened_ttl')]
        assert len(shortened) == 1 and shortened[0]['name'] == label + '-1'
        ttl = shortened[0]['shortened_ttl']
        assert type(ttl['before_ms']) is int and type(ttl['after_ms']) is int
        assert 0 < ttl['after_ms'] <= ttl['before_ms'] <= 60000
    assert indexed['misrouted-v2']['rejected'] is True
    assert indexed['misrouted-v2']['reason'] == 'upstream_mismatch'
    assert indexed['misrouted-v2']['managed_state_preserved'] is True
    assert indexed['misrouted-v2']['cache_preserved'] is True
    assert indexed['misrouted-v2']['durable_checks_before_rejection'] is True


def main():
    require_production_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--go-source', type=Path, default=ROOT)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'quality-reports/go-migration/impersonation-management-image')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.expected_revision):
        parser.error('--expected-revision must be a full Git revision')
    args.output = args.output.resolve()
    args.go_source = args.go_source.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'scope': 'current-schema packaged impersonation HTTP; not installed lifecycle',
              'complete_impersonation_parity': False, 'cleanup_verified': False,
              'private_directory_removed': False, 'source_sha256': source_hashes(),
              'native_repository_groups': {'status': 'not_run', 'required_separately': True,
                  'reason': 'Five actual Unix-socket PostgreSQL groups are a separate native gate.'},
              'started_at': datetime.now(timezone.utc).isoformat()}
    report_path = args.output / 'results.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    started = time.monotonic()
    build = None
    proof = None
    work = None
    cleanup_errors = []
    try:
        build = json.loads(args.build_receipt.read_text())
        assert isinstance(build, dict), 'Build receipt must be an object'
        assert build['success'] and build['source_stable']
        assert build['source_revision'] == args.expected_revision
        assert build_source_matches(args.go_source, build['source_sha256']), 'Image build source differs'
        tool_env = {key: value for key, value in os.environ.items()
                    if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
        tool_env.update(TESTS_IMPERSONATION_MANAGEMENT_GO='1',
                        TESTS_IMPERSONATION_MANAGEMENT_VERSIONED='1',
                        TESTS_IMPERSONATION_MANAGEMENT_GO_SOURCE=str(args.go_source))
        with tempfile.TemporaryDirectory(prefix='az-impersonation-image-', dir='/tmp') as directory, \
                (args.output / 'services.log').open('w') as log:
            work = Path(directory)
            proof = ImageFixture(args.image, args.expected_revision, work, args.output, log)
            try:
                proof.start_services(args.postgres, args.redis)
                assert proof.image_id == build['image_id'], 'Immutable image differs from build receipt'
                initialize_current_schema(work, '127.0.0.1', proof.postgres_port, DATABASE, log)
                from django.test import override_settings
                from TESTS.adapters.django.impersonation_management import run
                from TESTS.adapters.redis_faults import deny_container_redis_command
                container_id = proof.inspect(proof.redis)['Id']
                network_id = json.loads(proof.docker('network', 'inspect', proof.network).stdout)[0]['Id']

                def redis_fault_factory(address, fixture_work, command):
                    assert fixture_work == work and address == f'127.0.0.1:{proof.redis_port}'
                    return deny_container_redis_command(address, command,
                        container_id=container_id, owner_label=LABEL,
                        owner_value=proof.identifier, network_id=network_id)

                cache = {'default': {'BACKEND': 'django_redis.cache.RedisCache',
                    'LOCATION': f'redis://127.0.0.1:{proof.redis_port}/1',
                    'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                                'PASSWORD': 'synthetic-identity-only'}}}
                with override_settings(CACHES=cache):
                    result = run(work, log, tool_env, f'127.0.0.1:{proof.redis_port}',
                                 go_server_factory=proof.server, redis_fault_factory=redis_fault_factory)
                report['oracle'] = result
                assert_workloads(result, proof.image_id)
                report['checks_passed'] = True
            finally:
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
        if cleanup_errors:
            raise RuntimeError('Packaged fixture cleanup failed; see typed receipt')
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        report.update(cleanup_errors=cleanup_errors,
                      cleanup_verified=bool(proof and proof.cleanup_verified and not cleanup_errors),
                      private_directory_removed=bool(work and not work.exists()),
                      image=proof.metadata if proof else {},
                      source_stable=source_hashes() == report['source_sha256'],
                      build_source_stable=bool(isinstance(build, dict) and build_source_matches(args.go_source, build.get('source_sha256'))),
                      elapsed_seconds=round(time.monotonic() - started, 3),
                      finished_at=datetime.now(timezone.utc).isoformat())
        report['success'] = bool(report.get('checks_passed') and report['cleanup_verified']
                                 and report['private_directory_removed'] and report['source_stable']
                                 and report['build_source_stable'] and 'error_type' not in report)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
    assert report['success'], 'Packaged impersonation checks incomplete; see results.json'
    print(json.dumps({'success': True, 'image': proof.image_id,
                      'http_cases_per_full_workload': report['oracle']['workloads'][0]['cases'],
                      'cleanup_verified': True}))


if __name__ == '__main__':
    main()
