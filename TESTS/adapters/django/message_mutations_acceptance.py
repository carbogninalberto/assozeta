"""Parent-only saved-message acceptance; importing this module starts nothing."""
import argparse
import copy
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.parse import urlsplit
from uuid import UUID

from TESTS.adapters.go.message_image_execution import (
    ExecutionDeadline, begin_cleanup, bounded_seconds, defer_interruption,
)
from TESTS.contracts import test_message_mutations as contract
from TESTS.adapters.django.production_runtime import (
    production_python_version, require_production_python,
)

ROOT = Path(__file__).resolve().parents[3]
DOCUMENT = 'docs/architecture/go-migration/message_mutations_acceptance.md'
REFERENCE = 'docs/architecture/go-migration/message_mutations_reference.md'
TOOLS = ('postgres', 'initdb', 'pg_ctl', 'createdb', 'psql', 'redis-server', 'go')
SERVICES = ('postgres', 'redis', 'django', 'go', 'caddy', 'caddy-misroute')
FIXTURE = ('rows', 'cache', 'sequences', 'private_fixture')
SENSITIVE = ('tokenized_fixture_removed', 'private_sql_log_removed', 'private_query_observation_removed')
CHECKS = ('response', 'state', 'audit', 'cache', 'attribution')
HASH = re.compile(r'[0-9a-f]{64}\Z')


class AcceptanceError(Exception):
    """Only authored fixed codes may cross the public boundary."""


def need(condition, code):
    if not condition:
        raise AcceptanceError(code)


# libpq bounds cover setup, migration and every new Python connection, including
# partial django.setup(). Database defaults below also cover the Go connection.
PG_BOUNDS = {'PGCONNECT_TIMEOUT': '5',
             'PGOPTIONS': '-c statement_timeout=30000 -c lock_timeout=5000'}


@contextmanager
def private_diagnostics(log):
    """Capture Python streams and inherited native FDs; always restore both."""
    with ExitStack() as restore:
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        for fd in (1, 2):
            original = os.dup(fd)
            restore.callback(os.close, original)
            restore.callback(os.dup2, original, fd)
            os.dup2(log.fileno(), fd)
        # ExitStack restores every FD even if flushing or one restore fails.
        restore.callback(log.flush)
        with redirect_stdout(log), redirect_stderr(log):
            yield


@contextmanager
def _owned(lifetime):
    """Defer cancellation across existing bounded ownership transitions.

    Register exit before delivering a deferred startup signal. During teardown a
    first signal is delivered only after the current owner's exit has finished.
    Business failures declare terminal cleanup before unwinding other owners.
    """
    with ExitStack() as stack:
        try:
            with defer_interruption():
                value = stack.enter_context(lifetime)
            yield value
        except BaseException:
            begin_cleanup()
            raise
        finally:
            with defer_interruption():
                stack.close()


def configure_database_bounds(connections):
    """Set current and future session bounds before migrations or snapshots."""
    connection = connections['default']
    connection.settings_dict.setdefault('OPTIONS', {}).update(
        connect_timeout=5, options=PG_BOUNDS['PGOPTIONS'])
    with connection.cursor() as cursor:
        for setting, value in (('statement_timeout', 30000), ('lock_timeout', 5000)):
            cursor.execute(f'SET {setting} = {value}')
            name = connection.ops.quote_name(connection.settings_dict['NAME'])
            cursor.execute(f'ALTER DATABASE {name} SET {setting} = {value}')


def check_caddy_image(tools):
    """Inspect the exact shipped image locally; never pull or start a container."""
    image = re.search(r'^FROM (caddy:\S+) AS production$',
                      (ROOT / 'UI/Dockerfile').read_text(), re.M)
    need(image is not None, 'preflight.caddy-image')
    result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image[1]],
                            env=tools, capture_output=True, text=True, timeout=15, check=False)
    need(result.returncode == 0 and re.fullmatch(r'sha256:[0-9a-f]{64}\s*', result.stdout) is not None,
         'preflight.caddy-image')
    return result.stdout.strip().removeprefix('sha256:')


def _hash(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def source_hashes():
    from TESTS.adapters.django import message_mutations_runtime as mutation
    result = mutation.source_hashes()
    result[DOCUMENT] = hashlib.sha256((ROOT / DOCUMENT).read_bytes()).hexdigest()
    return result


def _runtime_sources(sources):
    return {key: value for key, value in sources.items() if key != DOCUMENT}


def _go_sources(sources):
    # Existing go_service hashes the transitive Python/Go graph, without oracle docs.
    return {key: value for key, value in sources.items() if key not in (DOCUMENT, REFERENCE)}


def required_go_version():
    match = re.search(r'^go (\d+\.\d+(?:\.\d+)?)$', (ROOT / 'BE_GO/go.mod').read_text(), re.M)
    need(match is not None, 'preflight.go-mod')
    return match[1]


def _version(value):
    parts = tuple(int(part) for part in value.split('.'))
    return parts + (0,) * (3 - len(parts))


def check_go_toolchain(tools):
    """No automatic toolchain download, installation or alternate Go search."""
    need(tools.get('GOTOOLCHAIN') == 'local', 'preflight.go-environment')
    try:
        result = subprocess.run(['go', 'version'], cwd=ROOT / 'BE_GO', env=tools,
                                capture_output=True, text=True, timeout=15, check=False)
        match = re.fullmatch(r'go version go(\d+\.\d+\.\d+) [a-z0-9]+/[a-z0-9]+\s*', result.stdout)
        required = required_go_version()
        need(result.returncode == 0 and match is not None, 'preflight.go-version')
        need(_version(match[1]) >= _version(required), 'preflight.go-version')
        return {'required': required, 'actual': match[1], 'local': True}
    except AcceptanceError:
        raise
    except BaseException:
        raise AcceptanceError('preflight.go-version') from None


def preflight(report):
    need(__debug__ and not sys.flags.optimize, 'preflight.python-assertions')
    # No application/service imports or tool execution before the exact Python gate.
    report['python'] = {'python': require_production_python()['python'],
                        'sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()}
    from TESTS.adapters.django import message_reads_runtime as reuse
    from TESTS.adapters.django.message_read_scopes_runtime import owned_tool_environment
    tools = owned_tool_environment()
    tools.update(PG_BOUNDS)
    reuse.apply_private_environment(tools)  # Including LANG/LC_ALL before initdb.
    report['go_toolchain'] = check_go_toolchain(tools)
    report['service_binary_sha256'] = {
        name: reuse.sha256_file(Path(path))
        for name, path in reuse.service_binary_paths(report['versioned']).items()
    }
    if report['versioned']:
        report['preflight_caddy_image_sha256'] = check_caddy_image(tools)
    report['source_sha256'] = source_hashes()
    return tools


def _steps(value, names):
    return (isinstance(value, dict) and value.get('verified') is True
            and isinstance(value.get('results'), dict) and set(value['results']) == set(names)
            and all(item is True for item in value['results'].values()))


def validate_report(report):
    """Validate every required receipt; a top-level success flag has no authority.

    This is an in-process gate over evidence generated by this CLI, not a verifier
    for arbitrary externally supplied JSON or proof that a synthetic test ran HTTP.
    """
    try:
        need(__debug__ and not sys.flags.optimize, 'receipt.assertions')
        need(type(report['versioned']) is bool, 'receipt.versioned')
        versioned = report['versioned']
        labels = ('django-native', 'go-native') + (('caddy-v1', 'caddy-v2') if versioned else ())
        backends = ('django', 'go') * (2 if versioned else 1)
        need(report['errors'] == [] and not report.get('cleanup_errors', []) and report['runtime_executed'] is True
             and report['complete_message_family_parity'] is False, 'receipt.runtime')
        sources = report['source_sha256']
        need(isinstance(sources, dict) and bool(sources) and all(_hash(v) for v in sources.values())
             and sources == source_hashes() == report['source_sha256_after'] == report['build_source_sha256']
             and report['go_build_source_sha256'] == _go_sources(sources)
             and report['source_unchanged'] is True, 'receipt.source')
        need(report['schema'] == {'applied_migrations': 548, 'remaining_plan': 0,
                                 'mode': 'full-migration-history'}
             and type(report['schema']['applied_migrations']) is int
             and type(report['schema']['remaining_plan']) is int
             and report['audit_resolvers_after_migration'] is True, 'receipt.schema')
        need(report['python']['python'] == production_python_version()
             and _hash(report['python']['sha256']), 'receipt.python')
        go = report['go_toolchain']
        need(go['local'] is True and go['required'] == required_go_version()
             and re.fullmatch(r'\d+\.\d+\.\d+', go['actual']) is not None
             and _version(go['actual']) >= _version(go['required']), 'receipt.go-version')
        need(set(report['service_binary_sha256']) == set(TOOLS + (('docker',) if versioned else ()))
             and all(_hash(v) for v in report['service_binary_sha256'].values()), 'receipt.tools')
        artifact = report['go_artifact']
        need(_hash(artifact['server_sha256']) and _hash(artifact['codec_sha256'])
             and type(artifact['process_exit']) is int and artifact['process_exit'] == 0
             and report['artifact_after_sha256'] == {key: artifact[key] for key in ('server_sha256', 'codec_sha256')},
             'receipt.artifact')
        need(set(report['service_cleanup']) == set(SERVICES), 'receipt.services')
        for name in SERVICES:
            expected = 'stopped' if versioned or name not in ('caddy', 'caddy-misroute') else 'not_started'
            need(report['service_cleanup'][name] == expected, 'receipt.services')
        need(all(report[key] is True for key in ('owned_directory_removed', 'private_directory_removed',
                    'environment_restored', 'umask_restored')), 'receipt.cleanup')
        need(_steps(report['resource_cleanup'], ('redis_client', 'db_connections', 'cache_override'))
             and _steps(report['sensitive_cleanup'], SENSITIVE)
             and report['sensitive_cleanup']['errors'] == [], 'receipt.resources')
        runtime = report['runtime']
        need(runtime['success'] is True and runtime['runtime_executed'] is True
             and runtime['differential_executed'] is True and runtime['django_only'] is False
             and runtime['complete_message_family_parity'] is False, 'receipt.differential')
        source = contract.load_source()
        need(len(source['cases']) == 119 and runtime['authored_source_sha256'] == contract.source_digest(source),
             'receipt.authored-source')
        need(runtime['source_sha256'] == runtime['source_sha256_after'] == _runtime_sources(sources), 'receipt.runtime-source')
        need(runtime['fixture_cleanup_verified'] is True and len(runtime['fixture_cleanup']) == len(labels)
             and all(_steps(item, FIXTURE) for item in runtime['fixture_cleanup']), 'receipt.fixture-cleanup')
        workloads = runtime['workloads']
        need(tuple(item['label'] for item in workloads) == labels, 'receipt.labels')
        evidence = report['evidence']
        need(evidence['verified'] is True and evidence['cases_per_workload'] == 119
             and tuple(evidence['labels']) == labels and _hash(evidence['normalized_effects_sha256']), 'receipt.evidence')
        for workload, backend in zip(workloads, backends, strict=True):
            need(workload['backend'] == backend and workload['success'] is True, 'receipt.backend')
            need(workload['go_binary_sha256'] == (artifact['server_sha256'] if backend == 'go' else None), 'receipt.binary')
            need(workload['normalized_effects_sha256'] == evidence['normalized_effects_sha256'], 'receipt.effects')
            need(len(workload['cases']) == 119, 'receipt.case-count')
            for expected, actual in zip(source['cases'], workload['cases'], strict=True):
                need(actual['case'] == expected['name'] and type(actual['status']) is int
                     and actual['status'] == expected['status'] and actual['models'] == 128, 'receipt.case')
                need(set(actual['checks']) == set(CHECKS) and all(v is True for v in actual['checks'].values()), 'receipt.checks')
                need(all(_hash(actual[k]) for k in ('state_before_sha256', 'state_after_sha256', 'cache_sha256')), 'receipt.case-hashes')
        need(set(report['client_measurements']) == set(labels), 'receipt.client-address')
        for label, measurement in report['client_measurements'].items():
            address = ipaddress.ip_address(measurement['address'])
            native = label.endswith('native')
            need(measurement['status'] == (401 if native else 200)
                 and measurement['transport'] == ('native loopback TCP' if native else 'proxy-owned health remote_host')
                 and (address.is_loopback if native else True), 'receipt.client-address')
        if versioned:
            misroute = report['misroute_control']
            need(set(misroute) == {'rejected', 'actual_backend', 'status', 'state_and_cache_unchanged', 'process_verified'}
                 and all(misroute[key] is True for key in ('rejected', 'state_and_cache_unchanged', 'process_verified'))
                 and misroute['actual_backend'] == 'django' and type(misroute['status']) is int
                 and misroute['status'] == 401, 'receipt.misroute')
            need(_hash(report['caddy_image_sha256']), 'receipt.caddy-image')
    except AcceptanceError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, AssertionError, OSError):
        raise AcceptanceError('receipt.incomplete') from None


def measure_native(base_url):
    peer = http.client.HTTPConnection('127.0.0.1', urlsplit(base_url).port, timeout=10)
    try:
        peer.connect()
        address = peer.sock.getsockname()[0]
        peer.request('GET', '/communications/messages/add')
        response = peer.getresponse()
        response.read()
        need(response.status == 401 and ipaddress.ip_address(address).is_loopback, 'runtime.native-probe')
        return {'transport': 'native loopback TCP', 'address': address, 'status': 401}
    finally:
        peer.close()


def verify_observations(runtime, endpoints, work, *, started, finished):
    """Bind private full observations to every published case and attribution.

    Replay the authored full-state projection on raw observations and compare the
    actual normalized effects, not just a success flag. Original tight timestamp
    windows and exact tokenized-session binding remain the frozen oracle's job;
    replay additionally uses the outer execution window and repeat-session binding.
    """
    from TESTS.adapters.django import message_mutations as adapter
    from TESTS.adapters.django.historical_read_cache import HistoricalReadCacheEntry, HistoricalReadCacheSnapshot
    from types import MappingProxyType
    source = contract.load_source()
    need(len(runtime['workloads']) == len(endpoints), 'evidence.labels')
    effects_digest = None
    reference = None
    for index, (workload, endpoint) in enumerate(zip(runtime['workloads'], endpoints, strict=True)):
        need(workload['label'] == endpoint['label'] and workload['backend'] == endpoint['backend'], 'evidence.backend')
        path = work / f'observations-{index}.jsonl'
        need(not path.is_symlink() and path.is_file() and path.stat().st_mode & 0o777 == 0o600, 'evidence.private-file')
        count = 0
        effects, sessions = [], {}
        with path.open() as stream:
            for line, case, receipt in zip(stream, source['cases'], workload['cases'], strict=True):
                observed = json.loads(line)
                need(observed['case'] == receipt['case'] == case['name']
                     and observed['status'] == receipt['status'] == case['status'], 'evidence.case')
                need(len(observed['before']) == len(observed['after']) == receipt['models'] == 128
                     and adapter.shared.digest(observed['before']) == receipt['state_before_sha256']
                     and adapter.shared.digest(observed['after']) == receipt['state_after_sha256'], 'evidence.state')
                need(observed['cache_before'] == observed['cache_after'], 'evidence.cache')
                entries = {}
                for entry in observed['cache_after']:
                    key = bytes.fromhex(entry['key_hex'])
                    need(key not in entries, 'evidence.cache')
                    entries[key] = HistoricalReadCacheEntry(bytes.fromhex(entry['type_hex']),
                        bytes.fromhex(entry['dump_hex']), entry['expires_at_ms'])
                need(HistoricalReadCacheSnapshot(MappingProxyType(entries)).digest == receipt['cache_sha256'], 'evidence.cache')
                adapter.shared.assert_attribution(endpoint['backend'], case['method'], case['path'],
                    receipt['status'], observed['observed'], case['name'])
                authored = copy.deepcopy(case)
                if case['audit_additional'] is not None and case['effect']['kind'] != 'none':
                    old_ids = {row['id'] for row in observed['before'][contract.AUDIT]}
                    added = [row for row in observed['after'][contract.AUDIT] if row['id'] not in old_ids]
                    need(len(added) == 1, 'evidence.audit')
                    session = added[0]['additional_data']['impersonation_session']
                    need(str(UUID(session)) == session, 'evidence.session')
                    label = case['audit_additional']['impersonation_session']
                    need(sessions.setdefault(label, session) == session, 'evidence.session')
                    authored['audit_additional']['impersonation_session'] = session
                effect = contract.assert_effect(authored, observed['before'], observed['after'],
                    started=started, finished=finished, remote_addr=endpoint['remote_addr'], cid=case['name'])
                if 'audit' in effect:
                    effect['audit']['remote_addr'] = '$independently-measured-client-ip'
                effects.append(effect)
                count += 1
        need(count == 119, 'evidence.case-count')
        digest = workload['normalized_effects_sha256']
        need(_hash(digest) and adapter.shared.digest(effects) == digest
             and (reference is None or effects == reference)
             and (effects_digest is None or digest == effects_digest), 'evidence.effects')
        effects_digest = digest
        reference = effects
    return {'verified': True, 'labels': [e['label'] for e in endpoints], 'cases_per_workload': 119,
            'normalized_effects_sha256': effects_digest}


def _misroute(report, gateway, observer, apps, raw, guard):
    from TESTS.adapters.django import message_mutations as adapter
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    from TESTS.http_client import Client
    initial = guard()
    need(initial['running'] is True, 'runtime.misroute-process')
    before = adapter.scopes.snapshot_state(apps)
    cache_before = snapshot_historical_read_cache(raw)
    cursor = observer.cursor()
    cid = 'message-mutation-misroute-probe'
    status, _, _ = Client(gateway + '/api/v2').request('GET', '/communications/messages/add',
        headers={'X-Correlation-ID': cid}, include_headers=True)
    need(status == 401, 'runtime.misroute-status')
    observed = observer.delta(cursor)
    adapter.shared.assert_attribution('django', 'GET', '/communications/messages/add', status, observed, cid)
    try:
        adapter.shared.assert_attribution('go', 'GET', '/communications/messages/add', status, observed, cid)
    except adapter.shared.AttributionError:
        pass
    else:
        raise AcceptanceError('runtime.misroute-not-rejected')
    adapter.shared.assert_state_unchanged(before, adapter.scopes.snapshot_state(apps))
    need(snapshot_historical_read_cache(raw) == cache_before and guard() == initial, 'runtime.misroute-state')
    report['misroute_control'] = {'rejected': True, 'actual_backend': 'django', 'status': status,
                                'state_and_cache_unchanged': True, 'process_verified': True}


def execute_private(report, tools, private, log):
    """Reuse the exact proven lifetimes; only a parent may invoke this with services."""
    from TESTS.adapters.django import message_reads_runtime as reuse
    from TESTS.adapters.django import message_mutations_runtime as mutation
    from TESTS.adapters.django.message_read_scopes_runtime import go_service, LIFECYCLE_GUARD, caddy_liveness_guard
    with _owned(reuse.owned_message_database(private, report)) as (work, port, _services):
        env, _ = reuse.private_environment(work, port, tools)
        env.update(PG_BOUNDS)
        reuse.apply_private_environment(env)
        raw, override = None, None
        resource = {'verified': False, 'results': {}}
        report['resource_cleanup'] = resource
        try:
            apps, _settings = reuse.initialize_django_apps()
            from django.db import connections
            from django.test.utils import override_settings
            from TESTS.http_client import poll, Client
            import redis
            configure_database_bounds(connections)
            override = override_settings(CACHES=reuse.cache_override(port))
            override.enable()
            report['schema'] = reuse.migrate_full_schema(log)
            from application.audit_resolvers import setup_audit_resolvers, get_resolver, direct_resolver
            from django.contrib.contenttypes.models import ContentType
            from communications.models import Message
            setup_audit_resolvers()
            need(get_resolver(ContentType.objects.get_for_model(Message)) is direct_resolver, 'runtime.audit-resolver')
            report['audit_resolvers_after_migration'] = True
            raw = redis.Redis(host='127.0.0.1', port=port, db=1, password='synthetic-identity-only',
                              decode_responses=False, socket_timeout=5, socket_connect_timeout=5)
            poll(raw.ping, bool, timeout=15)
            guards = []
            versioned = report['versioned']
            with _owned(reuse.django_application(report, versioned, None, liveness_observer=guards.append)) as (url, observer, http_port):
                report['client_measurements'] = {'django-native': measure_native(url)}
                report['build_source_sha256'] = source_hashes()
                need(report['build_source_sha256'] == report['source_sha256'], 'runtime.prebuild-source')
                with _owned(go_service(reuse, report, work, log, tools, port, versioned)) as native:
                    report['client_measurements']['go-native'] = measure_native(native['base_url'])
                    binary = work / 'payment-categories-server'
                    endpoints = [
                        {'label': 'django-native', 'backend': 'django', 'base_url': url,
                         'remote_addr': report['client_measurements']['django-native']['address'], 'process_guard': guards[0]},
                        {'label': 'go-native', 'backend': 'go', 'base_url': native['base_url'],
                         'remote_addr': report['client_measurements']['go-native']['address'],
                         'process_guard': native[LIFECYCLE_GUARD], 'binary': binary},
                    ]
                    def run():
                        started = time.time()
                        report['runtime'] = mutation.run(apps=apps, raw=raw, observer=observer,
                            private_work=private / 'observations', endpoints=endpoints)
                        finished = time.time()
                        report['evidence'] = verify_observations(report['runtime'], endpoints, private / 'observations',
                                                                started=started, finished=finished)
                        report['artifact_after_sha256'] = {'server_sha256': reuse.sha256_file(binary),
                            'codec_sha256': reuse.sha256_file(binary.with_name('image-codec'))}
                    if versioned:
                        upstream = f'host.docker.internal:{http_port}'
                        with _owned(reuse.owned_caddy(report, log, upstream, native['proxy_upstream'], pull_policy='never')) as (gateway, _image, container):
                            status, _, headers = Client(gateway).request('GET', '/healthz', include_headers=True)
                            address = headers['X-Fixture-Client-IP']
                            ipaddress.ip_address(address)
                            need(status == 200, 'runtime.proxy-health')
                            image = container['image_id'].removeprefix('sha256:')
                            need(_hash(image) and image == report['preflight_caddy_image_sha256'], 'runtime.proxy-image')
                            report['caddy_image_sha256'] = image
                            for label, backend, version, guard in (
                                    ('caddy-v1', 'django', 'v1', guards[0]),
                                    ('caddy-v2', 'go', 'v2', native[LIFECYCLE_GUARD])):
                                report['client_measurements'][label] = {'transport': 'proxy-owned health remote_host',
                                                                       'address': address, 'status': status}
                                endpoints.append({'label': label, 'backend': backend, 'base_url': gateway + '/api/' + version,
                                    'remote_addr': address, 'process_guard': caddy_liveness_guard(guard, container['container_id']),
                                    'binary': binary})
                            run()
                        with _owned(reuse.owned_caddy(report, log, upstream, upstream, service='caddy-misroute', pull_policy='never')) as (gateway, _, container):
                            _misroute(report, gateway, observer, apps, raw,
                                caddy_liveness_guard(guards[0], container['container_id']))
                    else:
                        run()
                    need(source_hashes() == report['build_source_sha256'], 'runtime.postbuild-source')
            report['runtime_executed'] = True
        finally:
            begin_cleanup()
            # All resource finalizers run, including after workload/teardown failure.
            def close_connections():
                # Import lazily even when initialization opened a connection then failed.
                from django.db import connections
                connections.close_all()

            for name, operation in (
                    ('redis_client', lambda: raw.close() if raw is not None else None),
                    ('db_connections', close_connections),
                    ('cache_override', lambda: override.disable() if override is not None else None)):
                try:
                    operation()
                    resource['results'][name] = True
                except BaseException:
                    resource['results'][name] = False
                    traceback.print_exc(file=log)
            resource['verified'] = all(resource['results'].values())


def public_receipt(report):
    """Construct a new allowlisted object; never copy arbitrary nested helper data."""
    result = {'success': report['success'], 'versioned': report['versioned'],
              'complete_message_family_parity': False, 'errors': list(report['errors']),
              'private_evidence': ('removed' if report.get('private_directory_removed') is True
                                   and report.get('owned_directory_removed') is True else 'cleanup-unverified')}
    for key in ('runtime_executed', 'source_unchanged', 'owned_directory_removed', 'private_directory_removed',
                'environment_restored', 'umask_restored', 'audit_resolvers_after_migration'):
        result[key] = report.get(key) is True
    # Only source filenames authored by this checkout and SHA256 values are public.
    for key in ('source_sha256', 'source_sha256_after', 'build_source_sha256', 'go_build_source_sha256'):
        if key in report:
            result[key] = {name: value for name, value in report[key].items()
                           if _hash(value) and not Path(name).is_absolute() and '..' not in Path(name).parts}
    for key, fields in (
            ('python', ('python', 'sha256')), ('go_toolchain', ('required', 'actual', 'local')),
            ('schema', ('applied_migrations', 'remaining_plan', 'mode')),
            ('go_artifact', ('server_sha256', 'codec_sha256', 'process_exit')),
            ('artifact_after_sha256', ('server_sha256', 'codec_sha256')),
            ('evidence', ('verified', 'labels', 'cases_per_workload', 'normalized_effects_sha256')),
            ('misroute_control', ('rejected', 'actual_backend', 'status', 'state_and_cache_unchanged', 'process_verified'))):
        if key in report:
            result[key] = {name: report[key][name] for name in fields if name in report[key]}
    result['service_cleanup'] = {name: (state if state in ('not_started', 'start_attempted', 'running', 'stopped', 'cleanup_failed')
                                                else 'cleanup_failed')
                                 for name, state in report['service_cleanup'].items() if name in SERVICES}
    result['service_binary_sha256'] = {name: value for name, value in report.get('service_binary_sha256', {}).items()
                                     if name in TOOLS + ('docker',) and _hash(value)}
    for key, names in (('resource_cleanup', ('redis_client', 'db_connections', 'cache_override')), ('sensitive_cleanup', SENSITIVE)):
        if key in report:
            value = report[key]
            result[key] = {'verified': value.get('verified') is True,
                          'results': {name: value.get('results', {}).get(name) is True for name in names}}
            if key == 'sensitive_cleanup':
                result[key]['errors'] = ['cleanup.private-failed'] * len(value.get('errors', []))
    runtime = report.get('runtime', {})
    result['runtime'] = {key: runtime.get(key) is True for key in (
        'success', 'runtime_executed', 'differential_executed', 'fixture_cleanup_verified',
        'django_only', 'complete_message_family_parity')}
    for key in ('source_sha256', 'source_sha256_after'):
        if key in runtime:
            result['runtime'][key] = {name: value for name, value in runtime[key].items()
                if _hash(value) and not Path(name).is_absolute() and '..' not in Path(name).parts}
    if 'authored_source_sha256' in runtime:
        result['runtime']['authored_source_sha256'] = runtime['authored_source_sha256']
    result['runtime']['fixture_cleanup'] = [
        {'verified': item.get('verified') is True,
         'results': {name: item.get('results', {}).get(name) is True for name in FIXTURE}}
        for item in runtime.get('fixture_cleanup', [])]
    result['runtime']['workloads'] = []
    # Full state, audit, cache bytes, schema SQL, tokens, URLs and diagnostic paths
    # never enter this projection, on success or on a partial failure.
    for workload in runtime.get('workloads', []):
        selected = {key: workload[key] for key in ('label', 'backend', 'success', 'go_binary_sha256',
                    'normalized_effects_sha256') if key in workload}
        selected['cases'] = [{key: case[key] for key in ('case', 'status', 'models', 'checks',
            'state_before_sha256', 'state_after_sha256', 'cache_sha256') if key in case}
            for case in workload.get('cases', [])]
        result['runtime']['workloads'].append(selected)
    if 'caddy_image_sha256' in report:
        result['caddy_image_sha256'] = report['caddy_image_sha256']
    # These independently measured loopback/proxy IPs contain no credentials.
    result['client_measurements'] = {label: {key: value[key] for key in ('transport', 'status', 'address')}
        for label, value in report.get('client_measurements', {}).items()}
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise AcceptanceError('cli.arguments')


def _main(args, execution):
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except OSError:
        print(json.dumps({'success': False, 'errors': ['cli.fresh-output-required']}))
        return 1
    report = {'success': False, 'versioned': args.versioned, 'runtime_executed': False,
              'complete_message_family_parity': False, 'errors': [],
              'service_cleanup': dict.fromkeys(SERVICES, 'not_started')}
    original = dict(os.environ)
    mask = None
    private = None
    write_failed = False
    try:
        with defer_interruption():
            mask = os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix='az-message-mutations-private-') as directory:
            private = Path(directory)
            private.chmod(0o700)
            with (private / 'runtime.log').open('x') as log, private_diagnostics(log):
                try:
                    failure_code = 'preflight.failed'
                    tools = preflight(report)
                    failure_code = 'runtime.failed'
                    execute_private(report, tools, private, log)
                except BaseException:
                    # No exception text/type or helper diagnostic payload is public.
                    report['errors'].append(failure_code)
                    traceback.print_exc(file=log)
    except BaseException:
        report['errors'].append('cleanup.private-failed')
    finally:
        begin_cleanup()
        if execution.interrupted:
            report['errors'].append('execution.interrupted')
        os.environ.clear()
        os.environ.update(original)
        report['environment_restored'] = dict(os.environ) == original
        if mask is not None:
            os.umask(mask)
        report['umask_restored'] = True
        report['private_directory_removed'] = private is not None and not private.exists()
        report['success'] = False
        try:
            report['source_sha256_after'] = source_hashes()
            report['source_unchanged'] = report.get('source_sha256') == report['source_sha256_after']
            validate_report(report)
            need(not execution.interrupted, 'execution.interrupted')
            report['success'] = True
        except BaseException:
            report['errors'].append('receipt.invalid')
        try:
            (args.output / 'results.json').write_text(json.dumps(public_receipt(report), indent=2) + '\n')
        except BaseException:
            report['success'] = False
            report['errors'].append('receipt.write-failed')
            write_failed = True
    if execution.interrupted:
        report['success'] = False
    print(json.dumps({'success': report['success'], 'check': 'message-mutations.saved-message-119',
                      'errors': report['errors']}))
    return 0 if report['success'] and not write_failed else 1


def _unverified_failure(output, code):
    # Never infer cleanup after an execution-envelope failure or grace expiry.
    receipt = {'success': False, 'errors': [code],
               'private_evidence': 'cleanup-unverified', 'private_directory_removed': False,
               'owned_directory_removed': False, 'cleanup_verified': False}
    if output.is_dir():
        (output / 'results.json').write_text(json.dumps(receipt) + '\n')


def _escalation(output, execution):
    _unverified_failure(output, 'execution.cleanup-grace-expired')


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--versioned', action='store_true')
    parser.add_argument('--deadline', type=float, default=1500)
    parser.add_argument('--cleanup-grace', type=float, default=180)
    try:
        args = parser.parse_args(argv)
        bounded_seconds(args.deadline, 3600)
        bounded_seconds(args.cleanup_grace, 300)
    except (AcceptanceError, ValueError):
        print(json.dumps({'success': False, 'errors': ['cli.arguments']}))
        return 1
    # Freshness is checked before a deadline owner can write an escalation receipt.
    if args.output.exists():
        print(json.dumps({'success': False, 'errors': ['cli.fresh-output-required']}))
        return 1
    execution = ExecutionDeadline(args.deadline, args.cleanup_grace,
        on_escalation=lambda state: _escalation(args.output, state))
    try:
        with execution:
            try:
                code = _main(args, execution)
            finally:
                begin_cleanup()
    except BaseException:
        # Cover cancellation during setup/FD restoration and unavailable signal
        # ownership with the same fixed public boundary. Never infer cleanup.
        code = 'execution.interrupted' if execution.interrupted else 'execution.failed'
        try:
            _unverified_failure(args.output, code)
        except BaseException:
            pass
        print(json.dumps({'success': False, 'errors': [code]}))
        return 1
    if execution.interrupted:
        # Include signals during receipt writing/restoration in the final result.
        from TESTS.adapters.go.message_image_execution import fail_receipt
        try:
            fail_receipt(args.output / 'results.json', execution)
        except BaseException:
            print(json.dumps({'success': False, 'errors': ['receipt.write-failed']}))
        return 1
    return code


if __name__ == '__main__':
    raise SystemExit(main())
