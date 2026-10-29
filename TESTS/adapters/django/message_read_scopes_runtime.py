"""Parent-only private message authorization differential acceptance.

Nothing launches or mutates environment at import. Invoke with production Python
and --output in a fresh directory; --versioned uses the shipped Caddy helper.
This runner has no query-budget gate and does not alter the existing query framework.
"""
import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from TESTS.adapters.django import message_read_scopes as adapter
from TESTS.adapters.django import message_session_lifecycle as lifecycle
from TESTS.contracts import test_message_read_scopes as contract

ROOT = adapter.ROOT
OWNED_SOURCES = (
    'TESTS/fixtures/message_read_scopes.json',
    'TESTS/contracts/test_message_read_scopes.py',
    'TESTS/adapters/django/message_read_scopes.py',
    'TESTS/adapters/django/message_read_scopes_runtime.py',
    'TESTS/test_message_read_scopes.py',
)
FIXTURE_CLEANUP = ('private_fixture_removed', 'fixture_rows_removed',
                   'managed_state_restored', 'cache_restored')
HASH = re.compile(r'[0-9a-f]{64}\Z')
LIFECYCLE_GUARD = 'message_session_lifecycle_process'
CADDY_INSPECT_FORMAT = ('{"Id":{{json .Id}},"State":{"Running":{{json .State.Running}},'
                        '"StartedAt":{{json .State.StartedAt}}},"RestartCount":{{json .RestartCount}}}')


def require_assertions():
    if not __debug__ or sys.flags.optimize:
        raise RuntimeError('optimized-python')


def owned_tool_environment(environment=None):
    """Keep provisioned tool/cache paths; own flags and ignore ambient Go config.

    The shared allowlist drops compiler/CGO/pkg-config overrides. GOFLAGS must also
    be replaced: overlays, tags, modfiles and toolexec can bypass source binding.
    """
    from TESTS.adapters.django import message_reads_runtime as reuse
    tools = reuse.sanitized_tool_environment(environment)
    tools.update({'LANG': 'C', 'LC_ALL': 'C', 'GOPROXY': 'off', 'GOSUMDB': 'off',
                  'GOFLAGS': '-p=1', 'GOWORK': 'off', 'GOENV': 'off', 'GOTOOLCHAIN': 'local'})
    return tools


def source_hashes():
    from TESTS.adapters.django import message_reads_runtime as reuse
    paths = reuse.source_paths()
    # Include the transitive Python adapter graph and its authored fixtures.
    paths.update((ROOT / 'TESTS').rglob('*.py'))
    paths.update((ROOT / 'TESTS/fixtures').rglob('*.json'))
    paths.update(ROOT / name for name in OWNED_SOURCES)
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def binding_valid(before, after, build):
    return (isinstance(before, dict) and bool(before) and before == after == build
            and all(isinstance(value, str) and HASH.fullmatch(value) for value in before.values()))


def expected_labels(versioned):
    return ('django-native', 'go-native') + (('caddy-v1', 'caddy-v2') if versioned else ())


def _hash(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def acceptance(report):
    """Pure fail-closed decision. Synthetic offline receipts are rejection controls only."""
    try:
        require_assertions()
        need = contract.require
        need(report['runtime_executed'] is True and not report['errors'] and not report['cleanup_errors'], 'receipt.runtime')
        need(type(report['versioned']) is bool, 'receipt.versioned')
        need(report['complete_message_family_parity'] is False, 'receipt.bounded')
        need(binding_valid(report['source_sha256'], report['source_sha256_after'], report['go_build_source_sha256'])
             and report['source_sha256'] == source_hashes(), 'receipt.source')
        need(report['schema'] == {'applied_migrations': 548, 'remaining_plan': 0,
                                 'mode': 'full-migration-history'}, 'receipt.schema')
        need(report['cleanup_verified'] is True and report['owned_directory_removed'] is True
             and report['private_directory_removed'] is True and report['environment_restored'] is True, 'receipt.cleanup')
        cleanup = report['fixture_cleanup']
        need(cleanup['verified'] is True and set(cleanup['results']) == set(FIXTURE_CLEANUP)
             and all(value is True for value in cleanup['results'].values()), 'receipt.fixture')
        need(report['resource_cleanup']['verified'] is True
             and set(report['resource_cleanup']['results']) == {'redis_client', 'db_connections', 'cache_override'}
             and all(value is True for value in report['resource_cleanup']['results'].values()), 'receipt.resources')
        sensitive = report['sensitive_cleanup']
        need(sensitive['verified'] is True and not sensitive['errors']
             and all(sensitive['results'].get(key) is True for key in (
                 'tokenized_fixture_removed', 'private_sql_log_removed', 'private_query_observation_removed')), 'receipt.private')
        artifact = report['go_artifact']
        need(_hash(artifact['server_sha256']) and _hash(artifact['codec_sha256'])
             and type(artifact['process_exit']) is int and artifact['process_exit'] == 0, 'receipt.artifact')
        need(report['python']['python'] == '3.13.16' and _hash(report['python']['sha256']), 'receipt.python')
        need(all(_hash(value) for value in report['service_binary_sha256'].values())
             and set(report['service_binary_sha256']) == set(('postgres', 'initdb', 'pg_ctl', 'createdb', 'psql', 'redis-server', 'go')
                 + (('docker',) if report['versioned'] else ())), 'receipt.tools')
        services = ('django', 'go', 'postgres', 'redis') + (('caddy', 'caddy-misroute') if report['versioned'] else ())
        need(all(report['service_cleanup'].get(service) == 'stopped' for service in services), 'receipt.processes')
        cases = contract.load_source()['cases']
        need(tuple(workload['label'] for workload in report['workloads']) == expected_labels(report['versioned']), 'receipt.workloads')
        for workload in report['workloads']:
            need(workload['success'] is True and len(workload['cases']) == len(cases), 'receipt.complete')
            for case, result in zip(cases, workload['cases'], strict=True):
                need(result['case'] == case['name'] and type(result['status']) is int
                     and result['status'] == case['status'] and result['check'] == case['check'], 'receipt.case')
                need(result['models'] == 128 and set(result['checks']) == {'response', 'state', 'cache', 'attribution'}
                     and all(value is True for value in result['checks'].values()), 'receipt.observation')
                need(_hash(result['state_sha256']) and _hash(result['cache_sha256']), 'receipt.hash')
        need(tuple(workload['label'] for workload in report['lifecycle_workloads'])
             == expected_labels(report['versioned']), 'receipt.lifecycle-labels')
        need(all(lifecycle.acceptance(workload) for workload in report['lifecycle_workloads']),
             'receipt.lifecycle')
        for scopes_workload, lifecycle_workload in zip(
                report['workloads'], report['lifecycle_workloads'], strict=True):
            baseline = lifecycle_workload['baseline']
            # Each adjacent pair shares the exact fixture/cache baseline. Bind
            # every scopes observation to it, including the final scopes read.
            need(all(result['state_sha256'] == baseline['state_sha256']
                     and result['cache_sha256'] == baseline['cache_sha256']
                     for result in scopes_workload['cases']), 'receipt.paired-baseline')
        if report['versioned']:
            misroute = report['misroute_control']
            need(misroute['rejected'] is True and misroute['actual_django_events'] == 1
                 and misroute['state'] is True and misroute['cache'] is True
                 and misroute['status'] == 200, 'receipt.misroute')
            need(_hash(report['caddy_image_sha256']), 'receipt.caddy-image')
        return True
    except (AssertionError, KeyError, TypeError, ValueError, RuntimeError, OSError):
        return False


def go_liveness_guard(process):
    """Use the Popen actually observed by the existing native factory."""
    pid = process.pid

    def guard():
        return {'pid': pid, 'instance': id(process),
                'running': bool(process.poll() is None and process.pid == pid)}

    return guard


def inspect_owned_caddy(container_id):
    """Inspect only bounded private identity fields; never publish Docker output."""
    from TESTS.adapters.django import message_reads_runtime as reuse
    contract.require(_hash(container_id), 'lifecycle.gateway-id')
    inspected = subprocess.run(
        ['docker', 'inspect', '--type', 'container', '--format', CADDY_INSPECT_FORMAT, container_id],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
        env=reuse.sanitized_tool_environment(),
    )
    contract.require(inspected.returncode == 0 and len(inspected.stdout) <= 8192,
                     'lifecycle.gateway-inspect')
    return json.loads(inspected.stdout)


def _caddy_identity(value, container_id):
    contract.require(type(value) is dict and set(value) == {'Id', 'State', 'RestartCount'},
                     'lifecycle.gateway-fields')
    state = value['State']
    contract.require(value['Id'] == container_id and type(state) is dict
                     and set(state) == {'Running', 'StartedAt'} and state['Running'] is True,
                     'lifecycle.gateway-running')
    started = state['StartedAt']
    contract.require(type(started) is str and re.fullmatch(
        r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z', started)
        and not started.startswith('0001-') and type(value['RestartCount']) is int
        and value['RestartCount'] >= 0, 'lifecycle.gateway-start')
    return (container_id, started, value['RestartCount'])


def caddy_liveness_guard(backend_guard, container_id, *, inspector=None):
    """Bind backend handles and the existing owned container without managing it.

    Injection is for offline controls only. The runner always uses Docker inspect.
    """
    inspect = inspect_owned_caddy if inspector is None else inspector
    try:
        contract.require(_hash(container_id), 'lifecycle.gateway-id')
        backend = lifecycle._process(backend_guard)
        identity = _caddy_identity(inspect(container_id), container_id)
        instance = int(hashlib.sha256(json.dumps(
            [backend['instance'], backend['pid'], *identity], separators=(',', ':'),
        ).encode()).hexdigest(), 16) or 1
    except BaseException:
        raise AssertionError('lifecycle.gateway-guard') from None

    def guard():
        running = False
        try:
            current_backend = lifecycle._process(backend_guard)
            current_identity = _caddy_identity(inspect(container_id), container_id)
            running = current_backend == backend and current_identity == identity
        except BaseException:
            pass
        return {'pid': backend['pid'], 'instance': instance, 'running': running}

    return guard


def run_workloads(label, backend, base_url, fixture, apps, raw, observer, guard, report):
    """Adjacent scopes/lifecycle workloads, sharing the same service and baseline."""
    adapter.run_workload(label, backend, base_url, fixture, apps, raw, observer,
                         workloads=report['workloads'])
    lifecycle_fixture = dict(fixture)
    lifecycle_fixture[LIFECYCLE_GUARD] = guard
    result = lifecycle.run_workload(label, backend, base_url, lifecycle_fixture, apps, raw, observer)
    report['lifecycle_workloads'].append(result)
    contract.require(lifecycle.acceptance(result), 'lifecycle.workload-failed')


def misroute_control(case, base_url, observer, fixture, apps, raw):
    """A real v2-to-Django request must be rejected by the actual ASGI delta."""
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    before_state, before_cache = adapter.snapshot_state(apps), snapshot_historical_read_cache(raw)
    cursor = observer.cursor()
    try:
        status = contract.assert_case(case, base_url)
    finally:
        after_state, after_cache = adapter.snapshot_state(apps), snapshot_historical_read_cache(raw)
        adapter.shared.assert_state_unchanged(before_state, after_state)
        adapter.shared.assert_cache_unchanged(before_cache, after_cache, fixture['contrasts'])
    observed = observer.delta(cursor)
    rejected = False
    try:
        adapter.shared.assert_attribution('go', 'GET', case['path'], status, observed, case['name'])
    except adapter.shared.AttributionError:
        rejected = True
    # Also prove exactly the expected Django request, not an arbitrary observer error.
    adapter.shared.assert_attribution('django', 'GET', case['path'], status, observed, case['name'])
    contract.require(rejected, 'misroute.must-reject')
    return {'rejected': True, 'actual_django_events': len(observed), 'state': True,
            'cache': True, 'status': status}


@contextmanager
def go_service(reuse, report, work, log, tool_environment, redis_port, versioned):
    from TESTS.adapters.go.payment_category_server import native_server
    process = None

    def remember(value):
        nonlocal process
        process = value

    report['service_cleanup']['go'] = 'start_attempted'
    try:
        report['go_build_source_sha256'] = source_hashes()
        with native_server(work, log, owned_tool_environment(tool_environment), f'127.0.0.1:{redis_port}',
                           versioned=versioned, process_observer=remember) as native:
            contract.require(process is not None and process.poll() is None, 'go.process')
            executable = Path(process.args[0]).resolve()
            contract.require(executable.parent == work.resolve(), 'go.artifact-directory')
            report['go_artifact'] = {'server_sha256': reuse.sha256_file(executable),
                                    'codec_sha256': reuse.sha256_file(executable.with_name('image-codec'))}
            contract.require(source_hashes() == report['go_build_source_sha256'], 'go.build-source-drift')
            report['service_cleanup']['go'] = 'running'
            yield {**native, LIFECYCLE_GUARD: go_liveness_guard(process)}
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    from TESTS.adapters.django.historical_read_routes import stop_process
                    stop_process(process)
                report.setdefault('go_artifact', {})['process_exit'] = process.returncode
                report['service_cleanup']['go'] = 'stopped' if process.poll() is not None else 'cleanup_failed'
            except BaseException:
                report['service_cleanup']['go'] = 'cleanup_failed'
                raise
        # A factory failure before observing a process cannot satisfy acceptance.


def execute_private(reuse, args, report, private_output, private_values, tool_environment):
    """Own state restoration in finally, including failed/partial fixture setup."""
    with reuse.owned_message_database(private_output, report) as (work, redis_port, log):
        environment, generated = reuse.private_environment(work, redis_port, tool_environment)
        private_values.update(generated)
        reuse.apply_private_environment(environment)
        with redirect_stdout(log), redirect_stderr(log):
            apps, _ = reuse.initialize_django_apps()
            from django.db import connections
            from django.test.utils import override_settings
            import redis
            from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
            override = override_settings(CACHES=reuse.cache_override(redis_port))
            raw, fixture, original_state, original_cache = None, None, None, None
            private_fixture = work / 'message-reads-private'
            try:
                override.enable()
                connections.close_all()
                report['schema'] = reuse.migrate_full_schema(log)
                contract.require(report['schema']['applied_migrations'] == 548, 'schema.exact-migrations')
                original_state = adapter.snapshot_state(apps)
                raw = redis.Redis(host='127.0.0.1', port=redis_port, db=1,
                                  password='synthetic-identity-only', decode_responses=False,
                                  socket_timeout=5, socket_connect_timeout=5)
                from TESTS.http_client import poll
                poll(raw.ping, bool, timeout=15)
                original_cache = snapshot_historical_read_cache(raw)
                fixture = adapter.seed_fixture(apps, private_fixture, raw)
                private_values.update(fixture['tokens'].values())
                private_values.update(fixture['sessions'].values())
                django_guard = None

                def remember_django_guard(guard):
                    nonlocal django_guard
                    django_guard = guard

                with reuse.django_application(report, args.versioned, None,
                        liveness_observer=remember_django_guard) as (django_base, observer, django_port):
                    run_workloads('django-native', 'django', django_base, fixture, apps, raw, observer,
                                  django_guard, report)
                    with go_service(reuse, report, work, log, tool_environment,
                                    redis_port, args.versioned) as native:
                        run_workloads('go-native', 'go', native['base_url'], fixture, apps, raw, observer,
                                      native.get(LIFECYCLE_GUARD), report)
                        if args.versioned:
                            django_upstream = f'host.docker.internal:{django_port}'
                            with reuse.owned_caddy(report, log, django_upstream, native['proxy_upstream']) as (gateway, image, container):
                                image_id = container['image_id'].removeprefix('sha256:')
                                contract.require(_hash(image_id), 'caddy.image-binding')
                                report['caddy_image_sha256'] = image_id
                                # Capture both guards before either workload; a restart
                                # between v1 and v2 must not establish a new baseline.
                                gateway_guards = {
                                    'django': caddy_liveness_guard(django_guard, container.get('container_id')),
                                    'go': caddy_liveness_guard(native.get(LIFECYCLE_GUARD), container.get('container_id')),
                                }
                                for label, backend, version in (('caddy-v1', 'django', 'v1'), ('caddy-v2', 'go', 'v2')):
                                    run_workloads(label, backend, gateway+'/api/'+version, fixture, apps, raw,
                                                  observer, gateway_guards[backend], report)
                            with reuse.owned_caddy(report, log, django_upstream, django_upstream,
                                                  service='caddy-misroute') as (gateway, _, __):
                                report['misroute_control'] = misroute_control(fixture['runtime']['cases'][0],
                                    gateway+'/api/v2', observer, fixture, apps, raw)
                report['runtime_executed'] = True
            finally:
                steps = [
                    ('private_fixture_removed', lambda: adapter.remove_private_fixture(private_fixture)),
                    ('fixture_rows_removed', lambda: adapter.shared.delete_created(fixture) if fixture is not None else True),
                    ('managed_state_restored', lambda: original_state is not None and adapter.snapshot_state(apps) == original_state),
                    ('cache_restored', lambda: original_cache is not None and adapter.restore_cache(raw, original_cache)),
                ]
                report['fixture_cleanup'] = adapter.cleanup_steps(steps)
                report['resource_cleanup'] = adapter.cleanup_steps((
                    ('redis_client', lambda: raw.close() if raw is not None else True),
                    ('db_connections', connections.close_all),
                    ('cache_override', override.disable),
                ))


def main(argv=None):
    require_assertions()  # Before directories, imports with backend effects or services.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--versioned', action='store_true')
    args = parser.parse_args(argv)
    # A fresh public destination is mandatory; never overwrite another acceptance.
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'success': False, 'runtime_executed': False, 'versioned': args.versioned,
              'errors': [], 'cleanup_errors': [], 'workloads': [], 'lifecycle_workloads': [],
              'complete_message_family_parity': False, 'cleanup_verified': False,
              'service_cleanup': dict.fromkeys(('django', 'go', 'postgres', 'redis', 'caddy', 'caddy-misroute'), 'not_started')}
    original_environment = dict(os.environ)
    private_values = set()
    private_directory = None
    try:
        from TESTS.adapters.django.production_runtime import require_production_python
        report['python'] = require_production_python()
        report['python'].pop('source', None)
        report['python'].pop('scope', None)
        from TESTS.adapters.django import message_reads_runtime as reuse
        report['python']['sha256'] = reuse.sha256_file(Path(sys.executable))
        report['service_binary_sha256'] = {name: reuse.sha256_file(Path(path)) for name, path
                                          in reuse.service_binary_paths(args.versioned).items()}
        report['source_sha256'] = source_hashes()
        tools = owned_tool_environment()
        reuse.apply_private_environment(tools)
        with tempfile.TemporaryDirectory(prefix='az-message-scopes-private-', dir='/tmp') as temporary:
            private_directory = Path(temporary)
            execute_private(reuse, args, report, private_directory, private_values, tools)
    except BaseException as error:
        # Public errors are fixed codes. No backend exception text, tokens, state or SQL.
        report['errors'].append(adapter.failure_diagnostic(error))
    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        report['environment_restored'] = dict(os.environ) == original_environment
        report['private_directory_removed'] = private_directory is not None and not private_directory.exists()
        try:
            report['source_sha256_after'] = source_hashes()
        except BaseException:
            report['errors'].append('source.binding-failed')
        report['cleanup_verified'] = (report.get('fixture_cleanup', {}).get('verified') is True
            and report.get('resource_cleanup', {}).get('verified') is True
            and report.get('sensitive_cleanup', {}).get('verified') is True
            and report.get('owned_directory_removed') is True and report['private_directory_removed']
            and report['environment_restored'])
        report['success'] = acceptance(report)
        # Restrict public receipt to codes, counts, statuses, booleans and hashes.
        # The reused lifetime helper can add private startup diagnostic paths.
        allowed = {'success', 'runtime_executed', 'versioned', 'errors', 'cleanup_errors', 'workloads', 'lifecycle_workloads',
            'complete_message_family_parity', 'cleanup_verified', 'service_cleanup', 'python',
            'service_binary_sha256', 'source_sha256', 'source_sha256_after', 'go_build_source_sha256',
            'schema', 'fixture_cleanup', 'resource_cleanup', 'sensitive_cleanup', 'owned_directory_removed',
            'private_directory_removed', 'environment_restored', 'go_artifact', 'caddy_image_sha256', 'misroute_control'}
        report = {key: value for key, value in report.items() if key in allowed}
        # Reused cleanup error strings are not publication-safe.
        report['cleanup_errors'] = ['cleanup.failed'] * len(report['cleanup_errors'])
        if 'sensitive_cleanup' in report:
            report['sensitive_cleanup']['errors'] = ['cleanup.private-failed'] * len(report['sensitive_cleanup']['errors'])
        adapter.shared.write_publishable(args.output / 'results.json', report, private_values)
    print(json.dumps({'success': report['success'], 'check': 'message-scopes.bounded-runtime'}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
