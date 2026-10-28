"""Replay the strict historical import oracle with one immutable Go image.

Each invocation owns one empty private PostgreSQL/Redis fixture and requires
native transports followed by both shipped Caddy API versions. Run each first
backend in a separate process. This is bounded packaged Go evidence, not an
installed lifecycle, packaged Django import, or whole read-family claim.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import MappingProxyType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]

from TESTS.adapters.django import historical_read_routes as oracle
from TESTS.adapters.go.payment_category_image import DATABASE, LABEL, ImageFixture
from TESTS.adapters.go.read_route_locales_image import (
    LICENSE_PATH, _container_bytes, license_evidence, verify_binary_digest,
)

GO_REQUIRED_FILES = ('Dockerfile', 'go.mod', 'go.sum', 'cmd/server/main.go',
                     'THIRD_PARTY_NOTICES.md')
# Full Django migration history for this frozen historical source.
PINNED_APPLIED_MIGRATIONS = 548
CLOCK_QUERY = 'SELECT clock_timestamp()'
CLOCK_DOMAIN = 'shared Docker Linux kernel CLOCK_REALTIME'
BOOT_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
EXTRA_SOURCES = (
    'TESTS/adapters/go/historical_read_image.py',
    'TESTS/adapters/go/test_historical_read_image.py',
    'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/read_route_locales_image.py',
    'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/go/impersonation_management_image.py',
    'TESTS/adapters/django/payment_category_shutdown.py',
)


def go_source_hashes(source):
    """Complete context, including frozen roots nested beneath quality-reports."""
    source = Path(source)
    context = source / 'BE_GO'
    assert context.is_dir(), 'Go source root must contain BE_GO'
    hashes = {path.relative_to(source).as_posix(): oracle.file_digest(path)
              for path in sorted(context.rglob('*'))
              if path.is_file() and '.git' not in path.relative_to(context).parts}
    assert all('BE_GO/' + name in hashes for name in GO_REQUIRED_FILES), 'Incomplete Go build context'
    return hashes


def source_hashes():
    hashes = oracle.source_hashes()
    # Include transitive test helpers, Django templates/translations and all
    # context assets, including embed payloads without a known file extension.
    paths = {ROOT / name for name in EXTRA_SOURCES}
    paths.update((ROOT / 'TESTS').rglob('*.py'))
    paths.update(path for path in (ROOT / 'BE').rglob('*')
                 if path.is_file() and path.suffix in ('.html', '.txt', '.po', '.mo'))
    hashes.update({path.relative_to(ROOT).as_posix(): oracle.file_digest(path)
                   for path in sorted(paths)})
    hashes.update(go_source_hashes(ROOT))
    return hashes


def verify_build_receipt(build, source, image_id, revision):
    assert isinstance(build, dict) and build.get('success') is True, 'Build receipt is not successful'
    assert build.get('source_stable') is True, 'Build source was not stable'
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', image_id or ''), 'Image must be immutable'
    assert re.fullmatch(r'[0-9a-f]{40}', revision or ''), 'Expected revision must be full length'
    assert build.get('image_id') == image_id, 'Image differs from build receipt'
    assert build.get('source_revision') == revision, 'Build revision differs'
    assert build.get('source_sha256') == go_source_hashes(source), 'Image build source differs'
    return verify_binary_digest(build.get('server_sha256'))


def verify_oracle_source(source):
    assert go_source_hashes(ROOT) == go_source_hashes(source), 'Image source differs from executing oracle Go inputs'


def assert_clock_evidence(result):
    clock = result.get('go_login_clock', {})
    source = clock.get('source', {})
    assert source.get('kind') == 'postgresql_clock_timestamp'
    assert source.get('query') == CLOCK_QUERY and source.get('database') == DATABASE
    assert source.get('clock_domain') == CLOCK_DOMAIN
    assert BOOT_ID.fullmatch(source.get('kernel_boot_id', ''))
    samples = clock.get('samples', [])
    assert len(samples) == 4, 'Both Go logins need before/after clock evidence'
    logins = [row for row in result['logins'] if row['backend'] == 'go']
    for index, (before, after) in enumerate(zip(samples[::2], samples[1::2])):
        cid = before['boundary'].removesuffix('.before')
        assert before['boundary'].endswith('.before')
        assert after['boundary'] == cid + '.after'
        assert cid.startswith('historical-' + logins[index]['workload'] + '-')
        lower, upper = (datetime.fromisoformat(row['timestamp']) for row in (before, after))
        observed = datetime.fromisoformat(logins[index]['last_login'])
        assert lower.utcoffset() is not None and upper.utcoffset() is not None
        assert lower <= observed <= upper, 'Go login escaped Linux clock bounds'
    times = [datetime.fromisoformat(row['timestamp']) for row in samples]
    assert times == sorted(times), 'Linux login clock moved backwards'


def assert_oracle(result, image_id, binary, first_backend):
    assert first_backend in ('django', 'go')
    for key in ('runtime_checks_passed', 'source_stable', 'cleanup_verified',
                'go_server_cleanup_verified', 'proxy_cleanup_verified',
                'final_login_projection_verified', 'import_worker_quiescent_before_baseline'):
        assert result.get(key) is True, key
    assert result.get('first_backend') == first_backend
    assert result.get('versioned') is True
    assert result.get('go_import_supported') is False
    assert result.get('whole_read_family_verified') is False
    assert result.get('historical_population', {}).get('exact') is True
    schema = result.get('schema', {})
    assert schema.get('mode') == 'full-migration-history' and schema.get('remaining_plan') == 0
    assert (type(schema.get('applied_migrations')) is int
            and schema['applied_migrations'] == PINNED_APPLIED_MIGRATIONS), (
        f'Historical source requires exactly {PINNED_APPLIED_MIGRATIONS} applied migrations')
    assert result.get('import', {}).get('success') is True
    assert result['import'].get('skipped_records') == oracle.SKIPS
    assert result['import'].get('warnings') == []
    assert result.get('configuration', {}).get('success') is True
    runtime = result.get('go_runtime', {})
    assert runtime.get('kind') == 'immutable-production-image'
    assert runtime.get('immutable_id') == image_id
    verify_binary_digest(runtime.get('server_sha256'), binary)
    for key in ('same_imported_database', 'readonly_root', 'health_version_matches_package',
                'kernel_clock_verified', 'notice_exact_bytes'):
        assert runtime.get(key) is True, key
    assert runtime.get('uid') == 10001 and runtime.get('user') == '10001:10001'
    assert runtime.get('mounted_executables') is False
    assert runtime.get('entrypoint') == ['/app/server']
    assert result.get('go_direct_request_headers') == {'X-Forwarded-For': '127.0.0.1'}
    specifications = oracle.expected_workloads(first_backend, True)
    assert result.get('workload_counts') == oracle.assert_workload_receipts(result['workloads'], specifications)
    for workload in result['workloads']:
        for read in workload['reads']:
            assert read.get('unchanged') is True and read.get('managed_models') == oracle.MODEL_COUNT
            oracle._cache_receipt(read.get('cache'))
            oracle._cache_receipt(read.get('cache_preflight'))
    for login in result.get('logins', []):
        assert login.get('exact_projection') is True
        oracle._cache_receipt(login.get('cache'))
    logins = result.get('logins', [])
    assert [row['workload'] for row in logins] == [spec['label'] for spec in specifications]
    assert logins[0]['password_upgrade']['performed'] is True
    assert all(row['password_upgrade']['performed'] is False and row['password_hash_preserved'] is True
               for row in logins[1:])
    assert result.get('first_go_login_initialization_exercised') is (first_backend == 'go')
    oracle._cache_receipt(result.get('final_cache'))
    versioned = result.get('versioned_runtime', {})
    assert versioned.get('routes') == {'/api/v1': 'django', '/api/v2': 'go'}
    assert versioned.get('same_imported_database') is True
    assert versioned.get('proxy_cleanup_verified') is True
    control = versioned.get('misroute_negative_control', {})
    for key in ('rejected', 'equivalent_response_verified', 'state_and_cache_preserved_before_rejection'):
        assert control.get(key) is True, key
    for row in (versioned, control):
        assert re.fullmatch(r'[0-9a-f]{64}', row.get('container_id', ''))
        assert re.fullmatch(r'sha256:[0-9a-f]{64}', row.get('image_id', ''))
    assert versioned['container_id'] != control['container_id']
    assert versioned['image_id'] == control['image_id']
    assert versioned['caddy_image'] == control['caddy_image']
    pinned_caddy = re.search(r'^FROM (caddy:\S+) AS production$',
                             (ROOT / 'UI/Dockerfile').read_text(), re.MULTILINE).group(1)
    assert versioned['caddy_image'] == pinned_caddy
    assert versioned.get('attribution') == {
        'v1_expected_django_requests': 8, 'v1_observed_django_requests': 8,
        'v2_expected_django_requests': 0, 'v2_observed_django_requests': 0}
    from TESTS.fixtures.historical_read_routes import load_contract
    manifest = load_contract()['http']['manifest']
    assert control.get('status') == manifest['status']
    assert control.get('label') == 'historical-caddy-v2-misrouted-to-django'
    assert control.get('expected_backend') == 'go' and control.get('observed_backend') == 'django'
    oracle.assert_request_attribution('django', 'GET', manifest['path'], manifest['status'],
                                      control['django_requests'], control['label'])
    assert_clock_evidence(result)
    return result


def assert_acceptance(report):
    for key in ('success', 'packaged_checks_passed', 'cleanup_verified', 'private_directory_removed',
                'source_stable', 'build_source_stable', 'build_receipt_stable'):
        assert report.get(key) is True, key
    assert report.get('cleanup_errors') == []
    assert report.get('both_orders_verified') is False
    assert report.get('packaged_django_verified') is False
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', report['image_id'])
    assert re.fullmatch(r'[0-9a-f]{40}', report['expected_revision'])
    build = report['build_receipt']
    assert build.get('success') is True and build.get('source_stable') is True
    assert build.get('image_id') == report['image_id']
    assert build.get('source_revision') == report['expected_revision']
    assert build.get('source_sha256') == report['build_context_sha256']
    verify_binary_digest(build.get('server_sha256'), report['binary_sha256'])
    image = report['image']
    assert image.get('immutable_id') == report['image_id']
    assert image.get('revision') == report['expected_revision']
    notice = report['third_party_license']
    assert notice.get('path') == LICENSE_PATH and notice.get('exact_bytes') is True
    assert notice.get('sha256') == report['third_party_license_source_sha256']
    assert notice.get('bytes') == report['third_party_license_bytes']
    assert_oracle(report['oracle'], report['image_id'], report['binary_sha256'], report['first_backend'])
    return report


def assert_both_orders(reports):
    """Parent review helper; two separate fresh fixture runs are mandatory."""
    assert len(reports) == 2
    for report in reports:
        assert_acceptance(report)
    assert {row['first_backend'] for row in reports} == {'django', 'go'}
    assert len({row['private_fixture_id'] for row in reports}) == 2
    for key in ('image_id', 'expected_revision', 'binary_sha256', 'source_sha256',
                'build_context_sha256', 'build_receipt_sha256'):
        assert reports[0][key] == reports[1][key], key
    return {'both_orders_verified': True, 'first_backends': ['django', 'go']}


class HistoricalImageFixture(ImageFixture):
    def audit_clock_source(self):
        kernel = self.docker('exec', self.postgres, 'cat', '/proc/sys/kernel/random/boot_id').stdout.strip()
        assert BOOT_ID.fullmatch(kernel)
        self.kernel = kernel
        return {'kind': 'postgresql_clock_timestamp', 'query': CLOCK_QUERY,
                'database': DATABASE, 'clock_domain': CLOCK_DOMAIN, 'kernel_boot_id': kernel}

    def read_audit_time(self):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(CLOCK_QUERY)
            return cursor.fetchone()[0]

    def verify_server(self, container, binary, revision, notice):
        info = self.inspect(container)
        assert info['Config']['Labels'].get(LABEL) == self.identifier
        assert info['State']['Running'] and info['Image'] == self.requested_image
        assert info['Config']['User'] == '10001:10001'
        assert info['Config']['Entrypoint'] == ['/app/server']
        assert info['HostConfig']['ReadonlyRootfs'] is True
        assert info['HostConfig']['CapDrop'] == ['ALL']
        assert 'no-new-privileges' in info['HostConfig']['SecurityOpt']
        # The inherited fixture mounts only its transient lifecycle directory.
        assert set(info['HostConfig']['Tmpfs']) == {'/tmp'}
        assert all((mount['Destination'], mount['Type']) in (('/operations', 'bind'), ('/tmp', 'tmpfs'))
                   for mount in info['Mounts'])
        assert self.docker('exec', container, 'id', '-u').stdout.strip() == '10001'
        assert self.docker('exec', container, 'cat', '/app/CODE_REVISION').stdout.strip() == revision
        verify_binary_digest(self.docker('exec', container, 'sha256sum', '/app/server').stdout.split()[0], binary)
        assert self.docker('exec', container, 'cat', '/proc/sys/kernel/random/boot_id').stdout.strip() == self.kernel
        self.notice_evidence = license_evidence(_container_bytes(container, LICENSE_PATH), notice)

    @contextmanager
    def verified_server(self, work, log, tool_env, address, *, versioned=False):
        assert versioned is True, 'Packaged acceptance requires both Caddy API versions'
        cleanup = {'verified': False}
        container = self.identifier + '-server'
        try:
            with self.server(work, log, tool_env, address, versioned=versioned) as runtime:
                self.verify_server(container, self.binary, self.revision, self.notice)
                metadata = {**runtime['metadata'], 'entrypoint': tuple(runtime['metadata']['entrypoint']),
                            'kind': 'immutable-production-image', 'kernel_clock_verified': True,
                            'notice_exact_bytes': True, 'kernel_boot_id': self.kernel}
                yield {**runtime, 'metadata': MappingProxyType(metadata), 'cleanup': cleanup,
                       'request_headers': MappingProxyType({'X-Forwarded-For': '127.0.0.1'}),
                       'verify_unchanged': lambda: self.verify_server(
                           container, self.binary, self.revision, self.notice)}
        finally:
            # Whole-fixture cleanup still runs if entry or verification failed.
            found = self.docker('inspect', container, check=False)
            if found.returncode == 0:
                info = json.loads(found.stdout)[0]
                assert info['Config']['Labels'].get(LABEL) == self.identifier, 'Server ownership changed'
                try:
                    logs = self.docker('logs', container, check=False)
                    assert not self.safe_log(self.output / 'server.log', logs.stdout + logs.stderr), (
                        'Fixture secret detected and redacted in server log')
                finally:
                    self.docker('rm', '--force', container)
            # A failed inspect alone cannot prove absence; independently query
            # the exact owned name across all container states.
            remaining = self.docker('ps', '--all', '--quiet', '--filter', 'name=^/' + container + '$')
            assert not remaining.stdout.strip(), 'Owned packaged server remains'
            cleanup['verified'] = True


def run_image(args):
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    assert not os.environ.get('TESTS_HISTORICAL_READ_FIXTURE'), 'Packaged oracle requires the bound repository overlay'
    assert args.output.resolve() != args.go_source.resolve()
    build = json.loads(args.build_receipt.read_text())
    binary = verify_build_receipt(build, args.go_source, args.image, args.expected_revision)
    verify_oracle_source(args.go_source)
    receipt_path = args.output / 'results.json'
    assert not receipt_path.exists(), 'Use a fresh output directory'
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'packaged_checks_passed': False, 'cleanup_verified': False,
              'both_orders_verified': False, 'packaged_django_verified': False,
              'scope': 'One order: supported Django historical import, packaged Go and actual Caddy v1/v2',
              'image_id': args.image, 'expected_revision': args.expected_revision,
              'first_backend': args.first_backend, 'binary_sha256': binary,
              'source_sha256': source_hashes(), 'build_context_sha256': go_source_hashes(args.go_source),
              'build_receipt_sha256': oracle.file_digest(args.build_receipt),
              'build_receipt': build, 'cleanup_errors': []}
    receipt_path.write_text(json.dumps(report, indent=2) + '\n')
    proof = work = None
    try:
        with tempfile.TemporaryDirectory(prefix='az-historical-image-', dir='/tmp') as directory, \
                (args.output / 'services.log').open('w') as log:
            work = Path(directory)
            proof = HistoricalImageFixture(args.image, args.expected_revision, work, args.output, log)
            proof.binary = binary
            proof.notice = (args.go_source / 'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes()
            report.update(private_fixture_id=proof.identifier,
                          third_party_license_source_sha256=hashlib.sha256(proof.notice).hexdigest(),
                          third_party_license_bytes=len(proof.notice))
            try:
                proof.start_services(args.postgres, args.redis)
                assert proof.image_id == args.image
                from TESTS.adapters.django.payment_category_shutdown import AuditClock
                clock = AuditClock(read=proof.read_audit_time, source=proof.audit_clock_source())
                from TESTS.fixtures.historical_read_routes import historical, load_contract, archive_bytes
                imported = {'workloads': [], 'first_backend': args.first_backend, 'versioned': True,
                            'go_import_supported': False, 'whole_read_family_verified': False,
                            'service_cleanup': {name: 'not_started' for name in oracle.SERVICES}}
                report['oracle'] = imported
                report['archive_sha256'] = hashlib.sha256(archive_bytes()).hexdigest()
                contract = load_contract()
                assert report['archive_sha256'] == contract['derived_archive_sha256']
                tool_env = {key: value for key, value in os.environ.items()
                            if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
                tool_env.update(LC_ALL='C', LANG='C')
                with patch.dict(os.environ, tool_env, clear=True):
                    oracle.run_runtime(work, proof.redis_port, log, imported, tool_env, historical(),
                                       contract, args.first_backend, versioned=True,
                                       go_server_factory=proof.verified_server, go_login_clock=clock,
                                       database={'NAME': DATABASE, 'HOST': '127.0.0.1',
                                                 'PORT': str(proof.postgres_port),
                                                 'USER': 'contracts', 'PASSWORD': 'synthetic-only'})
                imported['source_stable'] = report['source_sha256'] == source_hashes()
                # The outer fixture owns DB/Redis and directory teardown. Mark
                # this only in finalization after their positive cleanup evidence.
                report['third_party_license'] = proof.notice_evidence
                report['packaged_checks_passed'] = True
            finally:
                try:
                    if 'django.db' in sys.modules:
                        from django.conf import settings
                        from django.db import connections
                        # Importing a Django helper alone opens no connection.
                        # Runtime setup may fail before settings are configured.
                        if settings.configured:
                            connections.close_all()
                except BaseException as error:
                    report['cleanup_errors'].append({'stage': 'connections', 'error_type': type(error).__name__})
                try:
                    proof.cleanup()
                except BaseException as error:
                    report['cleanup_errors'].append({'stage': 'owned_fixture', 'error_type': type(error).__name__})
    except BaseException as error:
        report['error_type'] = type(error).__name__
        # Private diagnostics are deliberately excluded from uploadable receipts.
        import traceback
        (args.output / 'private-runner-error.log').write_text(traceback.format_exc())
    finally:
        for field, read, expected in (
                ('source_stable', source_hashes, report['source_sha256']),
                ('build_source_stable', lambda: go_source_hashes(args.go_source), report['build_context_sha256']),
                ('build_receipt_stable', lambda: oracle.file_digest(args.build_receipt), report['build_receipt_sha256'])):
            try:
                report[field] = read() == expected
            except BaseException as error:
                report[field] = False
                report['cleanup_errors'].append({'stage': field, 'error_type': type(error).__name__})
        report.update(image=proof.metadata if proof else {},
                      cleanup_verified=bool(proof and proof.cleanup_verified and not report['cleanup_errors']),
                      private_directory_removed=bool(work and not work.exists()))
        if 'oracle' in report:
            report['oracle']['cleanup_verified'] = bool(
                report['cleanup_verified'] and report['private_directory_removed']
                and all(report['oracle']['service_cleanup'][name] in ('not_started', 'stopped')
                        for name in ('worker', 'django', 'go', 'caddy')))
        report['success'] = bool(report['packaged_checks_passed'] and 'error_type' not in report)
        if report['success']:
            try:
                assert_acceptance(report)
            except BaseException as error:
                report.update(success=False, validation_error_type=type(error).__name__)
                import traceback
                (args.output / 'private-runner-error.log').write_text(traceback.format_exc())
        receipt_path.write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--first-backend', choices=('django', 'go'), required=True)
    parser.add_argument('--go-source', type=Path, default=ROOT)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image):
        parser.error('--image must be an exact immutable image ID')
    if not re.fullmatch(r'[0-9a-f]{40}', args.expected_revision):
        parser.error('--expected-revision must be a full Git revision')
    args.output, args.go_source, args.build_receipt = (
        path.resolve() for path in (args.output, args.go_source, args.build_receipt))
    report = run_image(args)
    if not report['success']:
        raise SystemExit(1)
    print(json.dumps({'success': True, 'first_backend': args.first_backend,
                      'both_orders_verified': False, 'output': str(args.output / 'results.json')}))


if __name__ == '__main__':
    main()
