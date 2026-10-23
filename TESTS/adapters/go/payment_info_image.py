"""Run the authored payment-info HTTP matrix against an immutable Go image.

Reuses the category image fixture's owned Docker PostgreSQL/Redis/network and
read-only non-root server. This is current-schema packaged HTTP evidence; the
six native Unix-socket repository groups remain a separate mandatory gate.
"""
import argparse
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

from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.payment_category_image import DATABASE, ImageFixture
from TESTS.contracts.test_payment_info import MATRIX_CASE_COUNT, MATRIX_VERSION

SOURCES = (
    'TESTS/adapters/go/payment_info_image.py',
    'TESTS/adapters/django/payment_info.py',
    'TESTS/adapters/django/payment_info_stats.py',
    'TESTS/contracts/test_payment_info.py',
    'TESTS/http_client.py',
    'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/django/settings.py',
    'TESTS/adapters/caddy.py',
    'selfhost/caddy/Caddyfile',
)


def source_hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}


def main():
    require_production_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path, default=ROOT / 'quality-reports/go-migration/payment-info-image')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.expected_revision):
        parser.error('--expected-revision must be a full Git revision')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        'success': False, 'scope': 'current-schema packaged payment-info HTTP; not installed lifecycle',
        'complete_payment_info_parity': False, 'cleanup_verified': False,
        'matrix_version': MATRIX_VERSION,
        'native_repository_groups': {'status': 'not_run', 'required_separately': True,
            'reason': 'This Docker image fixture runs HTTP contracts; the mandatory six Unix-socket PostgreSQL groups run in the native accounts gate.'},
        'started_at': datetime.now(timezone.utc).isoformat(),
        'baseline_source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_sha256': source_hashes(),
    }
    report_path = args.output / 'results.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    (args.output / 'http-results.json').write_text(
        json.dumps({'success': False, 'stage': 'not_started', 'started_at': report['started_at']}) + '\n')
    started = time.monotonic()
    tool_env = {key: value for key, value in os.environ.items()
                if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    oracle_started = False
    with tempfile.TemporaryDirectory(prefix='az-payment-info-image-', dir='/tmp') as directory, \
            (args.output / 'services.log').open('w') as log:
        work = Path(directory)
        proof = ImageFixture(args.image, args.expected_revision, work, args.output, log)
        try:
            proof.start_services(args.postgres, args.redis)
            initialize_current_schema(work, '127.0.0.1', proof.postgres_port, DATABASE, log)
            from django.test import override_settings
            from TESTS.adapters.django.payment_info import run

            os.environ.update(TESTS_PAYMENT_INFO_GO='1', TESTS_PAYMENT_INFO_VERSIONED='1')
            cache = {'default': {'BACKEND': 'django_redis.cache.RedisCache',
                'LOCATION': f'redis://127.0.0.1:{proof.redis_port}/1',
                'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                            'PASSWORD': 'synthetic-identity-only'}}}
            with override_settings(CACHES=cache):
                oracle_started = True
                result = run(work, log, tool_env, f'127.0.0.1:{proof.redis_port}',
                             go_server_factory=proof.server, output=work / 'oracle')
            assert result['success'] and result['cleanup_verified'] and result['proxy_cleanup_verified']
            assert result['http_cases'] == result['go_http_cases'] == MATRIX_CASE_COUNT
            assert result['versioned_http_cases'] == {'v1': MATRIX_CASE_COUNT, 'v2': MATRIX_CASE_COUNT}
            assert result['versioned_django_request_counts'] == {'v1': MATRIX_CASE_COUNT, 'v2': 0}
            assert result['routing_negative_control']['success']
            assert result['routing_negative_control']['rejected_reason'] == 'upstream_mismatch'
            assert result['go_and_proxy_database_writes'] == {'inserted': 0, 'updated': 0, 'deleted': 0}
            assert result['go_repository_integration'] is False and result['go_repository_groups'] == []
            assert result['go_repository_scope']['status'] == 'not_run'
            assert result['go_repository_scope']['required_separately'] is True
            assert result['go_runtime']['immutable_id'] == proof.image_id
            report['checks_passed'] = True
        except Exception as error:
            report['error_type'] = type(error).__name__
            raise
        finally:
            cleanup_errors = []
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            except Exception as error:
                cleanup_errors.append({'stage': 'database_connections', 'error_type': type(error).__name__})
            try:
                proof.cleanup()
            except Exception as error:
                cleanup_errors.append({'stage': 'docker_resources', 'error_type': type(error).__name__})
            finally:
                if oracle_started:
                    oracle_path = work / 'oracle/http-results.json'
                    if oracle_path.exists():
                        oracle = json.loads(oracle_path.read_text())
                        report['oracle'] = oracle
                        # Only safe result receipts are copied; the private token
                        # fixture remains excluded from exported evidence.
                        (args.output / 'http-results.json').write_text(json.dumps(oracle, indent=2) + '\n')
                report.update(image=proof.metadata, cleanup_verified=proof.cleanup_verified,
                    source_stable=report['source_sha256'] == source_hashes(),
                    finished_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic() - started)
                report['cleanup_errors'] = cleanup_errors
                report['success'] = bool(report.get('checks_passed') and proof.cleanup_verified
                                         and report['source_stable'] and not cleanup_errors)
                report_path.write_text(json.dumps(report, indent=2) + '\n')
            if cleanup_errors:
                raise RuntimeError('Packaged payment-info cleanup failed; see results.json')
    assert report['success'], 'Packaged payment-info proof did not satisfy all checks; see results.json'
    print(json.dumps({'success': report['success'], 'image': proof.metadata,
                      'http_cases_per_workload': MATRIX_CASE_COUNT, 'cleanup_verified': proof.cleanup_verified,
                      'native_repository_groups': 'not_run; required separately'}))


if __name__ == '__main__':
    main()
