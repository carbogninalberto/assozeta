"""Parent-only packaged message scopes and warm-session acceptance.

Consumes an existing immutable image and exact build receipt. No build or native
Go fallback exists. Import is inert; real services run only through main().
"""
import argparse
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from TESTS.adapters.django import message_read_scopes_runtime as runtime
from TESTS.adapters.django import message_read_scopes as adapter
from TESTS.adapters.django import message_session_lifecycle as lifecycle
from TESTS.contracts import test_message_read_scopes as contract
from TESTS.adapters.go.payment_category_image import DATABASE, LABEL, ImageFixture
from TESTS.adapters.go.message_image_execution import begin_cleanup
from TESTS.adapters.go.read_route_locales_image import (
    LICENSE_PATH, _container_bytes, license_evidence, verify_binary_digest,
)

EXPECTED_SCOPES = 38
EXPECTED_SEQUENCES = 2
GO_REQUIRED_FILES = ('Dockerfile', 'go.mod', 'go.sum', 'cmd/server/main.go',
                     'THIRD_PARTY_NOTICES.md')
SERVICES = ('django', 'go', 'postgres', 'redis', 'caddy', 'caddy-misroute')
RESOURCES = ('redis_client', 'db_connections', 'cache_override')
PRIVATE_CHECKS = ('tokenized_fixture_removed', 'private_sql_log_removed',
                  'private_query_observation_removed')
HASH = re.compile(r'[0-9a-f]{64}\Z')
need = contract.require


def require_assertions():
    runtime.require_assertions()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def go_source_hashes(source):
    """Bind every context file, including assets and frozen roots under reports."""
    require_assertions()
    source = Path(source)
    context = source / 'BE_GO'
    need(context.is_dir(), 'image.source-root')
    result = {path.relative_to(source).as_posix(): digest(path)
              for path in sorted(context.rglob('*'))
              if path.is_file() and '.git' not in path.relative_to(context).parts}
    need(all('BE_GO/' + name in result for name in GO_REQUIRED_FILES), 'image.source-inputs')
    return result


def source_hashes():
    result = runtime.source_hashes()
    # Keep all transitive fixture inputs, templates, translations and context
    # payloads, not just files with a Go/Python extension.
    paths = {path for path in (ROOT / 'BE').rglob('*') if path.is_file()}
    paths.update(ROOT / name for name in (
        'TESTS/adapters/go/message_read_scopes_image.py',
        'TESTS/adapters/go/test_message_read_scopes_image.py',
        'docs/architecture/go-migration/message_read_scopes_image.md'))
    result.update({path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(paths)
                   if '__pycache__' not in path.parts and path.suffix != '.pyc'})
    result.update(go_source_hashes(ROOT))
    return result


def verify_build_receipt(build, source, image_id, revision):
    require_assertions()
    need(type(build) is dict and build.get('success') is True
         and build.get('source_stable') is True, 'image.build-success')
    need(type(image_id) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', image_id), 'image.immutable')
    need(type(revision) is str and re.fullmatch(r'[0-9a-f]{40}', revision), 'image.revision')
    need(build.get('image_id') == image_id, 'image.build-image')
    need(build.get('source_revision') == revision, 'image.build-revision')
    need(build.get('source_sha256') == go_source_hashes(source), 'image.build-source')
    return verify_binary_digest(build.get('server_sha256'))


def _container_identity(value, image_id, owner):
    need(type(value) is dict, 'image.container-fields')
    state, config = value['State'], value['Config']
    need(type(value['Id']) is str and HASH.fullmatch(value['Id']), 'image.container-id')
    need(value['Image'] == image_id and config['User'] == '10001:10001'
         and config['Labels'][LABEL] == owner, 'image.container-ownership')
    need(state['Running'] is True and type(state['Pid']) is int and state['Pid'] > 0,
         'image.container-runtime')
    need(type(value['RestartCount']) is int and value['RestartCount'] == 0,
         'image.container-restarted')
    started = state['StartedAt']
    need(type(started) is str and re.fullmatch(
        r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z', started)
        and not started.startswith('0001-'), 'image.container-start')
    return {'id': value['Id'], 'image': image_id, 'pid': state['Pid'],
            'started': started, 'restarts': value['RestartCount']}


def container_liveness_guard(container, image_id, owner, *, inspector):
    """Fresh Docker identity AND backend Linux PID; never a fabricated Popen.

    Production passes the existing fixture's actual inspect operation. The
    injected inspector lets offline controls prove death/restart/ownership failure.
    """
    try:
        initial = _container_identity(inspector(container), image_id, owner)
    except (AssertionError, KeyError, TypeError, ValueError, OSError, RuntimeError,
            subprocess.SubprocessError):
        raise AssertionError('image.container-guard') from None
    instance = int(contract.source_digest(initial), 16) or 1

    def guard():
        running = False
        try:
            running = _container_identity(inspector(container), image_id, owner) == initial
        except (AssertionError, KeyError, TypeError, ValueError, OSError, RuntimeError,
                subprocess.SubprocessError):
            pass  # A failed observation is always a failed process check.
        return {'pid': initial['pid'], 'instance': instance, 'running': running}

    return guard


def gateway_liveness_guard(backend_guard, container_id):
    def inspect(identifier):
        value = runtime.inspect_owned_caddy(identifier)
        need(type(value['RestartCount']) is int and value['RestartCount'] == 0,
             'image.gateway-restarted')
        return value

    return runtime.caddy_liveness_guard(backend_guard, container_id, inspector=inspect)


@contextmanager
def owned_image_caddy(report, log, django, golang, *, network):
    """Compose the existing network-aware proxy lifetime without shared edits.

    ImageFixture owns the network. This adapter only observes the Caddy owned by
    proxy and verifies its removal, as the native owned_caddy helper does.
    """
    from TESTS.adapters.caddy import proxy
    report['service_cleanup']['caddy'] = 'start_attempted'
    container = {}
    stack = ExitStack()
    body_error = None
    try:
        gateway, image = stack.enter_context(proxy(
            django, golang, log, network=network, container_observer=container.update))
        need(_hash(container.get('container_id')), 'image.gateway-identity')
        report['service_cleanup']['caddy'] = 'running'
        yield gateway, image, container
    except BaseException as error:
        body_error = error
    try:
        stack.close()
        if container.get('container_id'):
            inspected = subprocess.run(['docker', 'inspect', container['container_id']],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            need(inspected.returncode == 1, 'image.gateway-cleanup')
    except BaseException:
        report['service_cleanup']['caddy'] = 'cleanup_failed'
        raise AssertionError('image.gateway-cleanup') from None
    report['service_cleanup']['caddy'] = 'stopped'
    if body_error is not None:
        raise body_error


class MessageImageFixture(ImageFixture):
    @contextmanager
    def verified_server(self, work, log, tools, build, report):
        name = self.identifier + '-server'
        with self.server(work, log, tools, f'127.0.0.1:{self.redis_port}', versioned=True) as server:
            verify_binary_digest(server['metadata']['server_sha256'], build['server_sha256'], build)
            report['third_party_license'] = license_evidence(
                _container_bytes(name, LICENSE_PATH),
                (report.pop('_go_source') / 'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes())
            first = self.inspect(name)
            identity = _container_identity(first, self.image_id, self.identifier)
            guard = container_liveness_guard(name, self.image_id, self.identifier, inspector=self.inspect)
            process = lifecycle._process(guard)
            report['container'] = {'identity_sha256': contract.source_digest(identity),
                'process_sha256': contract.source_digest(process), 'restart_count': 0,
                'running_verified': False, 'stopped_verified': False, 'exit_code': None}
            yield {**server, runtime.LIFECYCLE_GUARD: guard}
            need(lifecycle._process(guard) == process, 'image.container-lifetime')
            report['container']['running_verified'] = True
        # Inspect after the inherited lifetime helper actually stopped admission.
        stopped = self.inspect(name)
        need(stopped['Id'] == identity['id'] and stopped['Image'] == identity['image']
             and stopped['State']['StartedAt'] == identity['started']
             and stopped['RestartCount'] == 0 and stopped['State']['Running'] is False
             and type(stopped['State']['ExitCode']) is int and stopped['State']['ExitCode'] == 0,
             'image.container-stop')
        need(stopped['Config']['Labels'][LABEL] == self.identifier, 'image.container-ownership')
        report['container'].update(stopped_verified=True, exit_code=0)


def _hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def _cleanup_valid(receipt, names):
    return (receipt['verified'] is True and set(receipt['results']) == set(names)
            and all(value is True for value in receipt['results'].values()))


def runtime_acceptance(report):
    """Independent packaged workload decision; never rewrite native acceptance."""
    try:
        require_assertions()
        need(report['runtime_executed'] is True and report['versioned'] is True
             and not report['errors'] and not report['cleanup_errors'], 'receipt.runtime')
        need(report['complete_message_family_parity'] is False, 'receipt.bounded')
        sources = report['source_sha256']
        need(bool(sources) and sources == report['source_sha256_at_server_start']
             == report['source_sha256_after'] == source_hashes(), 'receipt.source')
        need(report['schema'] == {'applied_migrations': 547, 'remaining_plan': 0,
                                 'mode': 'full-migration-history'}, 'receipt.schema')
        need(report['python']['python'] == '3.13.16' and _hash(report['python']['sha256']), 'receipt.python')
        need(report['cleanup_verified'] is True and report['environment_restored'] is True
             and report['private_directory_removed'] is True, 'receipt.cleanup')
        need(all(report['service_cleanup'].get(name) == 'stopped' for name in SERVICES), 'receipt.services')
        need(_cleanup_valid(report['fixture_cleanup'], runtime.FIXTURE_CLEANUP)
             and _cleanup_valid(report['resource_cleanup'], RESOURCES)
             and _cleanup_valid(report['sensitive_cleanup'], PRIVATE_CHECKS)
             and not report['sensitive_cleanup']['errors'], 'receipt.restoration')
        source = contract.load_source()
        need(len(source['cases']) == EXPECTED_SCOPES, 'receipt.original38')
        labels = runtime.expected_labels(True)
        need(tuple(row['label'] for row in report['workloads']) == labels
             and tuple(row['label'] for row in report['lifecycle_workloads']) == labels, 'receipt.labels')
        baseline = None
        for workload, warm in zip(report['workloads'], report['lifecycle_workloads'], strict=True):
            need(workload['success'] is True and len(workload['cases']) == EXPECTED_SCOPES, 'receipt.scopes')
            need(lifecycle.acceptance(warm) and len(warm['scenarios']) == EXPECTED_SEQUENCES, 'receipt.lifecycle')
            baseline = warm['baseline'] if baseline is None else baseline
            need(warm['baseline'] == baseline, 'receipt.same-baseline')
            for authored, observed in zip(source['cases'], workload['cases'], strict=True):
                need(observed['case'] == authored['name'] and type(observed['status']) is int
                     and observed['status'] == authored['status'] and observed['check'] == authored['check'], 'receipt.case')
                need(type(observed['models']) is int and observed['models'] == 128
                     and set(observed['checks']) == {'response', 'state', 'cache', 'attribution'}
                     and all(value is True for value in observed['checks'].values()), 'receipt.observation')
                need(observed['state_sha256'] == baseline['state_sha256']
                     and observed['cache_sha256'] == baseline['cache_sha256'], 'receipt.paired-baseline')
        control = report['misroute_control']
        need(control == {'rejected': True, 'actual_django_events': 1, 'state': True,
                         'cache': True, 'status': 200} and _hash(report['caddy_image_sha256']), 'receipt.misroute')
        return True
    except (AssertionError, KeyError, TypeError, ValueError, RuntimeError, OSError):
        return False


def acceptance(report):
    """Image provenance, real runtime and owned cleanup are separate required gates."""
    try:
        require_assertions()
        need(report['success'] is True and report['packaged_checks_passed'] is True
             and report['runtime']['success'] is True and runtime_acceptance(report['runtime']), 'image.runtime')
        need(report['cleanup_verified'] is True and report['private_directory_removed'] is True
             and report['source_stable'] is True and report['build_source_stable'] is True
             and not report['errors'] and not report['cleanup_errors'], 'image.cleanup')
        image = report['image']
        build = report['build_receipt']
        need(re.fullmatch(r'sha256:[0-9a-f]{64}', report['requested_image'])
             and image['immutable_id'] == report['requested_image'] == build['image_id'], 'image.id')
        need(re.fullmatch(r'[0-9a-f]{40}', report['expected_revision'])
             and image['revision'] == report['expected_revision'] == build['source_revision'], 'image.revision')
        need(build['success'] is True and build['source_stable'] is True
             and _hash(report['build_receipt_sha256']), 'image.build')
        need(report['build_context_sha256'] == report['build_context_sha256_after']
             == build['source_sha256'] == go_source_hashes(ROOT), 'image.source')
        need(image['user'] == '10001:10001' and image['uid'] == 10001
             and image['entrypoint'] == ['/app/server'] and image['readonly_root'] is True
             and image['mounted_executables'] is False
             and image['health_version_matches_package'] is True and _hash(image['version_sha256'])
             and image['platform'] in ('linux/amd64', 'linux/arm64'), 'image.nonroot')
        verify_binary_digest(image['server_sha256'], build['server_sha256'], build)
        license_row = report['third_party_license']
        expected = (ROOT / 'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes()
        need(license_row == license_evidence(expected, expected), 'image.license')
        process = report['container']
        need(process['running_verified'] is True and process['stopped_verified'] is True
             and type(process['exit_code']) is int and process['exit_code'] == 0
             and type(process['restart_count']) is int and process['restart_count'] == 0
             and _hash(process['identity_sha256']) and _hash(process['process_sha256'])
             and process['process_sha256'] == report['runtime']['lifecycle_workloads'][1]['process_sha256'],
             'image.container-lifetime')
        need(report['complete_message_family_parity'] is False, 'image.bounded')
        return True
    except (AssertionError, KeyError, TypeError, ValueError, RuntimeError, OSError, IndexError):
        return False


def execute_workloads(reuse, proof, work, log, tools, build, report, private_values):
    """One seed, one credentials set and one exact DB1 baseline for all transports."""
    evidence = report['runtime']
    environment, generated = reuse.private_environment(work, proof.redis_port, tools)
    environment.update(TESTS_DATABASE=DATABASE, DBNAME=DATABASE, DBHOST='127.0.0.1',
                       DBPORT=str(proof.postgres_port))
    private_values.update(generated)
    reuse.apply_private_environment(environment)
    apps, _ = reuse.initialize_django_apps()
    from django.db import connections
    from django.test.utils import override_settings
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    from TESTS.http_client import poll
    import redis

    override = override_settings(CACHES=reuse.cache_override(proof.redis_port))
    raw = fixture = original_state = original_cache = None
    private_fixture = work / 'message-reads-private'
    try:
        override.enable()
        connections.close_all()
        evidence['schema'] = reuse.migrate_full_schema(log)
        need(evidence['schema'] == {'applied_migrations': 547, 'remaining_plan': 0,
                                   'mode': 'full-migration-history'}, 'schema.exact')
        original_state = adapter.snapshot_state(apps)
        raw = redis.Redis(host='127.0.0.1', port=proof.redis_port, db=1,
            password='synthetic-identity-only', decode_responses=False,
            socket_timeout=5, socket_connect_timeout=5)
        poll(raw.ping, bool, timeout=15)
        original_cache = snapshot_historical_read_cache(raw)
        fixture = adapter.seed_fixture(apps, private_fixture, raw)
        private_values.update(fixture['tokens'].values())
        private_values.update(fixture['sessions'].values())
        django_guards = []
        with reuse.django_application(evidence, True, None,
                liveness_observer=django_guards.append) as (django_base, observer, django_port):
            need(len(django_guards) == 1, 'image.django-guard')
            django_guard = django_guards[0]
            runtime.run_workloads('django-native', 'django', django_base, fixture,
                                 apps, raw, observer, django_guard, evidence)
            evidence['source_sha256_at_server_start'] = source_hashes()
            evidence['service_cleanup']['go'] = 'start_attempted'
            with proof.verified_server(work, log, tools, build, report) as server:
                evidence['service_cleanup']['go'] = 'running'
                go_guard = server[runtime.LIFECYCLE_GUARD]
                runtime.run_workloads('go-native', 'go', server['base_url'], fixture,
                                     apps, raw, observer, go_guard, evidence)
                django_upstream = f'host.docker.internal:{django_port}'
                with owned_image_caddy(evidence, log, django_upstream,
                        server['proxy_upstream'], network=proof.network) as (gateway, _, caddy):
                    evidence['caddy_image_sha256'] = caddy['image_id'].removeprefix('sha256:')
                    need(_hash(evidence['caddy_image_sha256']), 'image.caddy-binding')
                    guards = {'django': gateway_liveness_guard(django_guard, caddy['container_id']),
                              'go': gateway_liveness_guard(go_guard, caddy['container_id'])}
                    for label, backend, version in (('caddy-v1', 'django', 'v1'), ('caddy-v2', 'go', 'v2')):
                        runtime.run_workloads(label, backend, gateway + '/api/' + version,
                            fixture, apps, raw, observer, guards[backend], evidence)
                with reuse.owned_caddy(evidence, log, django_upstream, django_upstream,
                        service='caddy-misroute') as (gateway, _, __):
                    evidence['misroute_control'] = runtime.misroute_control(
                        fixture['runtime']['cases'][0], gateway + '/api/v2', observer, fixture, apps, raw)
            evidence['service_cleanup']['go'] = 'stopped'
        evidence['runtime_executed'] = True
    finally:
        begin_cleanup()
        evidence['fixture_cleanup'] = adapter.cleanup_steps((
            ('private_fixture_removed', lambda: adapter.remove_private_fixture(private_fixture)),
            ('fixture_rows_removed', lambda: adapter.shared.delete_created(fixture) if fixture is not None else True),
            ('managed_state_restored', lambda: original_state is not None and adapter.snapshot_state(apps) == original_state),
            ('cache_restored', lambda: original_cache is not None and adapter.restore_cache(raw, original_cache)),
        ))
        evidence['resource_cleanup'] = adapter.cleanup_steps((
            ('redis_client', lambda: raw.close() if raw is not None else True),
            ('db_connections', connections.close_all), ('cache_override', override.disable),
        ))


def main(argv=None):
    require_assertions()  # Before output creation, backend imports or side effects.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--go-source', type=Path, default=ROOT)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image):
        parser.error('--image must be an immutable image ID')
    if not re.fullmatch(r'[0-9a-f]{40}', args.expected_revision):
        parser.error('--expected-revision must be a full Git revision')
    args.go_source = args.go_source.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    original_environment = dict(os.environ)
    private_values = set()
    work = proof = None
    report = {'success': False, 'packaged_checks_passed': False,
        'scope': 'bounded packaged message scopes and warm-session lifecycle',
        'complete_message_family_parity': False, 'requested_image': args.image,
        'expected_revision': args.expected_revision, 'errors': [], 'cleanup_errors': [],
        'runtime': {'success': False, 'runtime_executed': False, 'versioned': True,
            'complete_message_family_parity': False, 'errors': [], 'cleanup_errors': [],
            'workloads': [], 'lifecycle_workloads': [],
            'service_cleanup': dict.fromkeys(SERVICES, 'not_started')}}
    evidence = report['runtime']
    # Reserve a fresh receipt before any attempt. Never read/relabel old results.
    receipt = args.output / 'results.json'
    receipt.write_text(json.dumps(report, indent=2) + '\n')
    try:
        from TESTS.adapters.django.production_runtime import require_production_python
        from TESTS.adapters.django import message_reads_runtime as reuse
        python = require_production_python()
        evidence['python'] = {'python': python['python'], 'sha256': digest(sys.executable)}
        evidence['source_sha256'] = source_hashes()
        report['build_context_sha256'] = go_source_hashes(args.go_source)
        need(report['build_context_sha256'] == go_source_hashes(ROOT), 'image.oracle-source')
        # Hash and decode the SAME bytes, and recheck the original file at exit.
        build_bytes = args.build_receipt.read_bytes()
        report['build_receipt_sha256'] = hashlib.sha256(build_bytes).hexdigest()
        build = json.loads(build_bytes)
        verify_build_receipt(build, args.go_source, args.image, args.expected_revision)
        # Retain all source inputs, publish only validated receipt fields.
        report['build_receipt'] = {key: build[key] for key in (
            'success', 'source_stable', 'source_revision', 'image_id', 'source_sha256', 'server_sha256')}
        tools = runtime.owned_tool_environment()
        reuse.apply_private_environment(tools)
        with tempfile.TemporaryDirectory(prefix='az-message-image-', dir='/tmp') as directory:
            work = Path(directory)
            private_output = work / 'private-logs'
            private_output.mkdir(mode=0o700)
            with (private_output / 'services.log').open('w') as log:
                proof = MessageImageFixture(args.image, args.expected_revision, work, private_output, log)
                report['_go_source'] = args.go_source
                try:
                    evidence['service_cleanup'].update(postgres='start_attempted', redis='start_attempted')
                    with redirect_stdout(log), redirect_stderr(log):
                        proof.start_services(args.postgres, args.redis)
                        need(proof.image_id == args.image, 'image.resolved-id')
                        evidence['service_cleanup'].update(postgres='running', redis='running')
                        execute_workloads(reuse, proof, work, log, tools, build, report, private_values)
                    report['packaged_checks_passed'] = True
                finally:
                    begin_cleanup()
                    # Helpers attempt each cleanup independently; failure never
                    # becomes success even if a later cleanup attempt succeeds.
                    try:
                        reuse.remove_sensitive_files(work, evidence)
                    except BaseException:
                        report['cleanup_errors'].append('cleanup.private-failed')
                    if 'sensitive_cleanup' in evidence:
                        sensitive = evidence['sensitive_cleanup']
                        sensitive['errors'] = ['cleanup.private-failed'] * len(sensitive['errors'])
                    try:
                        proof.private_values.update(private_values)
                        proof.cleanup()
                    except BaseException:
                        report['cleanup_errors'].append('cleanup.image-failed')
                    for service in ('postgres', 'redis'):
                        evidence['service_cleanup'][service] = 'stopped' if proof.cleanup_verified else 'cleanup_failed'
    except BaseException:
        report['errors'].append('image.execution-failed')
    finally:
        report.pop('_go_source', None)
        os.environ.clear()
        os.environ.update(original_environment)
        evidence['environment_restored'] = dict(os.environ) == original_environment
        report['private_directory_removed'] = evidence['private_directory_removed'] = bool(work and not work.exists())
        try:
            evidence['source_sha256_after'] = source_hashes()
            report['source_stable'] = evidence.get('source_sha256') == evidence['source_sha256_after']
            report['build_context_sha256_after'] = go_source_hashes(args.go_source)
            report['build_source_stable'] = (report.get('build_context_sha256') == report['build_context_sha256_after']
                and digest(args.build_receipt) == report.get('build_receipt_sha256'))
        except BaseException:
            report['errors'].append('image.source-binding-failed')
        metadata = proof.metadata if proof else {}
        report['image'] = {key: metadata[key] for key in (
            'immutable_id', 'requested_image', 'platform', 'user', 'entrypoint',
            'postgres_image', 'redis_image', 'revision', 'server_sha256',
            'readonly_root', 'uid', 'mounted_executables', 'health_version_matches_package')
            if key in metadata}
        if type(metadata.get('version')) is str:
            report['image']['version_sha256'] = hashlib.sha256(metadata['version'].encode()).hexdigest()
        report['cleanup_verified'] = bool(proof and proof.cleanup_verified and not report['cleanup_errors'])
        evidence['cleanup_verified'] = bool(report['cleanup_verified'] and evidence['private_directory_removed']
            and evidence['environment_restored'] and evidence.get('fixture_cleanup', {}).get('verified') is True
            and evidence.get('resource_cleanup', {}).get('verified') is True
            and evidence.get('sensitive_cleanup', {}).get('verified') is True)
        # Helpers may carry private diagnostic cleanup strings; expose fixed codes.
        evidence['cleanup_errors'] = ['cleanup.runtime-failed'] * len(evidence['cleanup_errors'])
        evidence['success'] = runtime_acceptance(evidence)
        report['success'] = True  # Candidate only; independent acceptance gates decide.
        report['success'] = acceptance(report)
        adapter.shared.write_publishable(receipt, report, private_values)
    print(json.dumps({'success': report['success'], 'check': 'message-scopes.packaged-image'}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
