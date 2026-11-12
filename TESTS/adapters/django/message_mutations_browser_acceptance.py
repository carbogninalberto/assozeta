"""Maintained saved-message browser composition. Importing is inert.

Consumes existing artifacts only; invoking main owns private native services.
Browser and separately authored HTTP denial verification remain independent.
"""
import argparse
import base64
from contextlib import ExitStack
import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading

from TESTS.adapters.django import message_reads_browser_runtime as s
from TESTS.adapters.django import message_mutations_browser as browser
from TESTS.adapters.django import message_mutations_browser_runtime as mutations
from TESTS.adapters.django import message_mutations_browser_denials as denials
from TESTS.adapters.django import message_mutations as fixture_api
from TESTS.adapters.go.message_image_execution import ExecutionDeadline

OWNED = ('TESTS/adapters/django/message_mutations_browser_acceptance.py',
         'TESTS/test_message_mutations_browser_acceptance.py',
         'docs/architecture/go-migration/message_mutations_browser_acceptance.md')
PATH_OPTIONS = ('ui_dir', 'build_observation', 'playwright_module', 'go_binary',
                'image_codec', 'go_build_record')
SERVICES = ('postgres', 'redis', 'django', 'go')


class CliError(RuntimeError):
    """Only fixed, safe diagnostics cross the CLI boundary."""


def need(value):
    if not value:
        raise CliError('receipt.invalid')


def parse_args(argv=None):
    # Delegate existing options and their ranges/path resolution to the read CLI.
    envelope = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    envelope.add_argument('--deadline', type=float, default=1500)
    envelope.add_argument('--cleanup-grace', type=float, default=180)
    try:
        extras, rest = envelope.parse_known_args(argv)
        ExecutionDeadline(extras.deadline, extras.cleanup_grace)
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()):
            args = s.parse_args(rest)
        args.deadline, args.cleanup_grace = extras.deadline, extras.cleanup_grace
        return args
    except SystemExit as error:
        if error.code == 0:
            raise
        raise CliError('cli.arguments') from None
    except Exception:
        raise CliError('cli.arguments') from None


def source_hashes():
    graph = s.source_hashes() | denials.source_hashes()
    # Conservative local helper closure also covers imports inside runtime functions.
    # UI/native compiler projections are still checked by their original validators.
    names = set(OWNED)
    for directory in ('TESTS/adapters', 'TESTS/contracts'):
        names.update(str(path.relative_to(s.ROOT)) for path in
                     (s.ROOT / directory).rglob('*.py'))
    graph.update({name: s.sha(s.ROOT / name) for name in sorted(names)})
    return graph


def freeze_inputs(args):
    """Read-only preflight: every input is checked before private service entry."""
    from TESTS.adapters.django.production_runtime import require_production_python
    python = require_production_python()
    need(__debug__)
    provenance = browser.capture_provenance(args.ui_dir)
    build = s.validate_build_record(args.build_observation, provenance)
    native = s.validate_go_build_record(args.go_build_record, args.go_binary, args.image_codec)
    modules = s.module_fingerprints(args.playwright_module)
    paths = s.runtime.service_binary_paths(False)
    bins = Path(paths['postgres']).parent
    paths.update(pg_dump=bins / 'pg_dump', node=args.node, server=args.go_binary, codec=args.image_codec)
    binaries = s.runtime.binary_hashes(paths)
    return {'python': python, 'sources': source_hashes(), 'provenance': provenance,
            'build': build, 'native': native, 'modules': modules, 'binaries': binaries,
            'paths': {name: str(getattr(args, name)) for name in (*PATH_OPTIONS, 'node')},
            'options': {'timeout': args.timeout, 'deadline': args.deadline,
                        'cleanup_grace': args.cleanup_grace}}


def recheck_inputs(args, frozen):
    need(frozen == freeze_inputs(args))


def initialize_bounded_django(settings):
    """Must run before django.setup(), including database access from app.ready()."""
    for database in settings.DATABASES.values():
        options = database.setdefault('OPTIONS', {})
        options['connect_timeout'] = 5
        options['options'] = (options.get('options', '') +
                              ' -c statement_timeout=30000 -c lock_timeout=5000').strip()
    return s.runtime.initialize_django_apps()


def measured_address(port, guard):
    before = copy.deepcopy(guard())
    need(before.get('running') is True and type(before.get('pid')) is int and
         before['pid'] > 0 and before.get('instance') is not None)
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.connect()
        address = connection.sock.getsockname()[0]
    finally:
        connection.close()
    need(address == '127.0.0.1' and guard() == before)
    return address


def invalidate(report, code, control=None, *, unverified=False):
    report.update(success=False, browser_acceptance=False, http_denials_verified=False)
    if code not in report['errors']:
        report['errors'].append(code)
    if control is not None:
        report['execution'] = {'reason': control.reason, 'escalated': control.escalated}
    if unverified:
        report.update(cleanup_verified=False, private_directory_removed=False)


def bounded_publish(writer):
    """Best effort only: a blocked filesystem cannot block grace-period hard exit."""
    def attempt():
        try:
            writer()
        except BaseException:
            pass
    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()
    thread.join(timeout=2)


def require_services_stopped_before_reset(report, recovery, publish, *, private_log_directory=None):
    if any(report['service_cleanup'].get(name) not in ('not_started', 'stopped')
           for name in ('django', 'go')):
        invalidate(report, 'cleanup.services-not-stopped', unverified=True)
        report['recovery_directory'] = str(recovery)
        if private_log_directory is not None:
            report['private_log_directory'] = str(private_log_directory)
        report['cleanup_errors'].append('cleanup.services-not-stopped')
        # Never unwind fixture_context, FixtureRestore or the outer private owner.
        # os._exit is intentional: Python context unwinding would reset live state.
        try:
            bounded_publish(publish)
        finally:
            os._exit(124)


def execute_versions(*, args, report, frozen, private, work, raw, apps, settings,
                     log, environment, tool_environment, secrets, publish):
    bins = Path(frozen['binaries']['postgres']['path']).parent
    for version in ('v1', 'v2'):
        report['service_cleanup'].update(django='not_started', go='not_started')
        cleanup = {'version': version}
        report['fixtures'].append(cleanup)
        with fixture_api.fixture_context(apps, work / ('fixture-' + version), raw,
                                         cleanup_receipt=cleanup) as fixture:
            restore = s.FixtureRestore(apps, raw, work, bins, log, environment)
            logo = Path(settings.MEDIA_ROOT) / 'instance/logo.png'
            need(not logo.exists() and not logo.is_symlink())
            expected_logo = hashlib.sha256(base64.b64decode(s.FIXTURE_PNG)).hexdigest()
            try:
                identities = s.prepare_browser_identities(apps, work, consumer='mutations')
                secrets.update(row['password'] for row in identities.values())
                secrets.update(fixture['tokens'].values())
                guards = {}
                with ExitStack() as services:
                    observer, django_port = services.enter_context(s.owned_django(report,
                        liveness_observer=lambda guard: guards.update(django=guard)))
                    go_port = services.enter_context(s.owned_go(work, args.go_binary,
                        args.image_codec, environment, log, report, frozen['native'],
                        liveness_observer=lambda guard: guards.update(go=guard)))
                    guard = guards['django' if version == 'v1' else 'go']
                    address = measured_address(django_port if version == 'v1' else go_port, guard)
                    report['denial_versions'].append(denials.run_denials(version=version,
                        apps=apps, raw=raw, fixture=fixture, observer=observer,
                        django_port=django_port, go_port=go_port, process_guard=guard,
                        remote_addr=address, timeout=120))
                    report['versions'].append(mutations.run_version(version=version, args=args,
                        work=work, private_log=log, environment=tool_environment,
                        identities=identities, apps=apps, raw=raw, fixture=fixture,
                        observer=observer, django_port=django_port, go_port=go_port,
                        process_guard=guard, remote_addr=address, report=report))
            finally:
                require_services_stopped_before_reset(report, work, publish, private_log_directory=private)
                try:
                    cleanup['login_restore'] = restore.restore()
                finally:
                    if logo.exists() or logo.is_symlink():
                        need(not logo.is_symlink() and s.sha(logo) == expected_logo)
                        logo.unlink()
                    cleanup['owned_logo_removed'] = not logo.exists() and not logo.is_symlink()


def close_client(report, code, close):
    """Attempt independent finalizers without replacing the primary exception."""
    try:
        close()
    except BaseException:
        report['cleanup_errors'].append(code)


def execute_private(args, report, frozen, private, secrets, publish):
    tool_environment = s.runtime.sanitized_tool_environment()
    tool_environment.update(LANG='C', LC_ALL='C', TZ='UTC', GOPROXY='off',
                            GOSUMDB='off', GOTOOLCHAIN='local')
    s.runtime.apply_private_environment(tool_environment)
    with s.private_database(private, report) as (work, redis_port, log):
        report['recovery_directory'] = str(work)
        environment, private_secrets = s.runtime.private_environment(work, redis_port, tool_environment)
        secrets.update(private_secrets)
        s.runtime.apply_private_environment(environment)
        # Lazy settings need the application's import root before settings access.
        sys.path[:0] = [str(s.ROOT / 'BE'), str(s.ROOT)]
        from django.conf import settings as bootstrap_settings
        from django.db import connections
        from django.core.cache import caches
        with ExitStack() as clients:
            # Register before setup/app.ready() can acquire any database handle.
            clients.callback(close_client, report, 'cleanup.database-client', connections.close_all)
            clients.callback(close_client, report, 'cleanup.cache-client', caches.close_all)
            cache_config = s.runtime.cache_override(redis_port)
            cache_config['default']['OPTIONS'].update(SOCKET_TIMEOUT=5, SOCKET_CONNECT_TIMEOUT=5)
            bootstrap_settings.CACHES = cache_config
            apps, settings = initialize_bounded_django(bootstrap_settings)
            from django.conf.global_settings import PASSWORD_HASHERS
            from django.test.utils import override_settings
            from TESTS.http_client import poll
            import redis
            connections.close_all()
            clients.enter_context(override_settings(CACHES=cache_config, PASSWORD_HASHERS=PASSWORD_HASHERS))
            raw = redis.Redis(host='127.0.0.1', port=redis_port, db=1,
                password='synthetic-identity-only', socket_timeout=5,
                socket_connect_timeout=5, retry_on_timeout=False)
            clients.callback(close_client, report, 'cleanup.redis-client', raw.close)
            def ready():
                try:
                    return raw.ping()
                except redis.ConnectionError:
                    return False
            poll(ready, bool, timeout=15)
            cache_client = caches['default'].client.get_client(write=True)
            for client in (raw, cache_client):
                need(all(client.connection_pool.connection_kwargs.get(key) == 5
                         for key in ('socket_timeout', 'socket_connect_timeout')))
            with connections['default'].cursor() as cursor:
                cursor.execute('SHOW statement_timeout')
                statement = cursor.fetchone()[0]
                cursor.execute('SHOW lock_timeout')
                lock = cursor.fetchone()[0]
            need(statement == '30s' and lock == '5s' and
                 connections['default'].get_connection_params()['connect_timeout'] == 5)
            report['private_io_bounds'] = dict(connect_seconds=5, redis_socket_seconds=5,
                statement_seconds=30, lock_seconds=5, execution_seconds=args.deadline,
                cleanup_grace_seconds=args.cleanup_grace)
            report['schema'] = s.runtime.migrate_full_schema(log)
            from application.audit_resolvers import setup_audit_resolvers, get_resolver, direct_resolver
            from django.contrib.contenttypes.models import ContentType
            from communications.models import Message
            setup_audit_resolvers()
            need(get_resolver(ContentType.objects.get_for_model(Message)) is direct_resolver)
            report['audit_resolvers_after_migration'] = True
            execute_versions(args=args, report=report, frozen=frozen, private=private,
                work=work, raw=raw, apps=apps, settings=settings, log=log,
                environment=environment, tool_environment=tool_environment,
                secrets=secrets, publish=publish)


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _hashes(value):
    return isinstance(value, dict) and bool(value) and all(
        isinstance(key, str) and _hash(digest) for key, digest in value.items())


def _models(value):
    need(isinstance(value, dict) and value.get('models') == 128 and
         type(value.get('rows')) is int and value['rows'] > 0 and _hash(value.get('sha256')))


def validate_report(report):
    """Validate a published summary's completeness/bindings, not private effects.

    Actual state/effect/guard/HTTP verification stays in the maintained verifiers.
    This checker cannot authenticate a fabricated receipt or reconstruct snapshots.
    Raises a fixed CliError for any incomplete or contradictory success evidence.
    """
    try:
        need(all(report.get(key) is True for key in
                 ('success', 'browser_acceptance', 'http_denials_verified')))
        need(isinstance(report, dict) and report.get('errors') == [] and report.get('cleanup_errors') == [])
        for key in ('cleanup_verified', 'inputs_stable', 'private_directory_removed',
                    'environment_restored', 'umask_restored', 'audit_resolvers_after_migration'):
            need(report.get(key) is True)
        need(report.get('complete_message_family_parity') is False)
        need(report['service_cleanup'] == dict.fromkeys(SERVICES, 'stopped'))
        need(report.get('owned_directory_removed') is True and report.get('postgres_stopped') is True
             and report.get('redis_stopped') is True)
        sensitive = report['sensitive_cleanup']
        need(sensitive['verified'] is True and sensitive['errors'] == [] and
             sensitive['results'] == dict.fromkeys(('tokenized_fixture_removed',
                 'private_sql_log_removed', 'private_query_observation_removed'), True))
        need(report.get('execution', {}).get('reason') is None)
        need(_hashes(report['source_sha256_before']) and
             report['source_sha256_before'] == report['source_sha256_after'])
        need(set(OWNED) <= set(report['source_sha256_before']))
        need(report['parent_driver_sha256'] == report['source_sha256_before'][OWNED[0]])
        schema = report['schema']
        need(schema['mode'] == 'full-migration-history' and schema['remaining_plan'] == 0 and
             type(schema['applied_migrations']) is int and schema['applied_migrations'] > 0)
        bounds = report['private_io_bounds']
        need(all(bounds.get(key) == value for key, value in dict(connect_seconds=5,
             redis_socket_seconds=5, statement_seconds=30, lock_seconds=5).items()))
        ExecutionDeadline(bounds['execution_seconds'], bounds['cleanup_grace_seconds'])
        frozen = report['input_binding']
        need(frozen['sources'] == report['source_sha256_before'])
        need(frozen['native'] == report['native_build'])
        provenance, build = frozen['provenance'], frozen['build']
        need(_hashes(provenance['artifact_sha256']) and _hashes(provenance['source_sha256']))
        need(build['artifact_sha256'] == provenance['artifact_sha256'] and
             build['source_sha256'] == {name: digest for name, digest in
                 provenance['source_sha256'].items() if name.startswith('UI/')})
        need(_hash(build['record_sha256']) and isinstance(build['invocation'], list) and
             build['invocation'] and all(isinstance(arg, str) and arg for arg in build['invocation']))
        need(_hashes(frozen['native']['artifacts']) and _hash(frozen['native']['record_sha256']) and
             _hashes(frozen['native']['source_sha256']) and _hashes(frozen['modules']['files']))
        need(set(frozen['paths']) == set((*PATH_OPTIONS, 'node')) and
             all(isinstance(path, str) and Path(path).is_absolute() for path in frozen['paths'].values()))
        need(isinstance(frozen['binaries'], dict) and frozen['binaries'] and all(
            Path(row['path']).is_absolute() and _hash(row['sha256']) for row in frozen['binaries'].values()))
        need(type(frozen['options']['timeout']) is int and 30 <= frozen['options']['timeout'] <= 900)
        need(frozen['options']['deadline'] == bounds['execution_seconds'] and
             frozen['options']['cleanup_grace'] == bounds['cleanup_grace_seconds'])
        fixtures = report['fixtures']
        need(isinstance(fixtures, list) and [row.get('version') for row in fixtures] == ['v1', 'v2'])
        for row in fixtures:
            need(row['verified'] is True and row['owned_logo_removed'] is True and
                 row['results'] == dict.fromkeys(('rows', 'cache', 'sequences', 'private_fixture'), True))
            restore = row['login_restore']
            need(restore['all_128_models_restored'] is True and restore['complete_db1_restored'] is True
                 and _hash(restore['cache_sha256']))
            _models(restore['models'])
        versions, denial_versions = report['versions'], report['denial_versions']
        need(isinstance(versions, list) and [row.get('version') for row in versions] == ['v1', 'v2'])
        need(isinstance(denial_versions, list) and
             [row.get('version') for row in denial_versions] == ['v1', 'v2'])
        authored = browser.authored_spec()
        for row in versions:
            version = row['version']
            need(row['acceptance'] == dict(success=True, version=version,
                checkpoints_verified=24, selected_requests_verified=26,
                complete_message_family_parity=False, http_denials_verified=False))
            for key in ('proxy_cleanup_verified', 'browser_child_reaped', 'real_cookie_forwarding'):
                need(row.get(key) is True)
            need(row['http_denials_verified'] is False and row['complete_message_family_parity'] is False
                 and row['fixture_cleanup_owner'] == 'parent')
            need(row['context_close_proofs'] == [{'actor': actor, 'observed_after_context_close': True}
                                                for actor in browser.ACTORS])
            _models(row['final_state_proof']['models'])
            cache = row['final_state_proof']['cache']
            need(cache['typed_values_exact'] is True and cache['absolute_expirations_exact'] is True and
                 cache['contrast_keys_verified'] == 2 and cache['readiness_prefix_excluded'] is False and
                 cache['unexpected_readiness_keys'] == 0 and _hash(cache['cache_sha256']))
            need(row['transport_probe'] == dict(cookies_preserved=True, post_body_preserved=True,
                                               redirect_blocked=True))
            need(_hash(row['browser_binary_sha256']) and _hash(row['playwright_files_sha256']))
            need(row['native_build'] == frozen['native'] and
                 row['playwright_version'] == frozen['modules']['version'] and
                 row['playwright_files_sha256'] ==
                 s.json_digest(dict(sorted(frozen['modules']['files'].items()))))
            need(row['provenance_before'] == row['provenance_after'] == frozen['provenance'])
            # Preserve the actual verifier's full proxy evidence, not just counts.
            selected = [event for event in row['proxy_observations'] if event.get('cid')]
            need(len(selected) == 26 and len({event['cid'] for event in selected}) == 26)
            need(all(event['backend'] == event['intended_backend'] ==
                     ('django-v1' if version == 'v1' else 'go-v2') and event['fallback'] is False
                     for event in selected))
            offset = 0
            for actor, action in browser.CHECKPOINTS:
                spec = authored['actors'][actor]
                plan = ([('POST', browser.LOGIN, 200)] if action == 'login'
                    else [] if action == 'closed' else
                    ([(spec[action]['method'], spec[action]['path'], spec[action]['status'])]
                     if action in ('create', 'delete') else []) +
                    [('GET', browser.LIST, 403 if actor == 'restricted_collaborator' else 200)])
                for method, path, status in plan:
                    event = selected[offset]
                    need(re.fullmatch(f'message-mutations-{version}-{actor}-{action}-[1-9][0-9]*',
                                      event['cid']) is not None and
                         (event['method'], event['path'], event['status']) == (method, path, status))
                    need(event['peer'] == event['intended_peer'] and event['peer'][0] == '127.0.0.1'
                         and type(event['peer'][1]) is int and 0 < event['peer'][1] < 65536)
                    offset += 1
            need(offset == 26)
            restricted = [event for event in selected if event['path'] == browser.LIST and
                          '-restricted_collaborator-read-' in event['cid']]
            need(len(restricted) == 1 and restricted[0]['status'] == 403)
        expected = authored['denials']
        for row in denial_versions:
            need(all(row.get(key) is True for key in
                     ('success', 'http_denials_verified', 'proxy_cleanup_verified')))
            need(row['models'] == 128 and row['browser_acceptance'] is False and
                 row['complete_message_family_parity'] is False and row['fixture_cleanup_owner'] == 'parent')
            denial_sources = set(browser.EXTRA_SOURCES) | {
                'TESTS/adapters/django/message_mutations_browser_denials.py',
                'TESTS/test_message_mutations_browser_denials.py',
                'TESTS/adapters/django/message_reads.py',
                'TESTS/adapters/django/historical_read_cache.py',
                'TESTS/adapters/django/production_runtime.py'}
            need(row['source_sha256'] == {name: frozen['sources'][name] for name in denial_sources})
            cases = row['cases']
            need(isinstance(cases, list) and len(cases) == 4)
            for actual, wanted in zip(cases, expected, strict=True):
                need(actual['verified'] is True and actual['case'] == wanted['name'] and
                     actual['status'] == wanted['status'] and actual['source'] == 'parent-http')
                need(all(_hash(actual.get(key)) for key in
                         ('response_sha256', 'wire_sha256', 'state_sha256', 'cache_sha256')))
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise CliError('receipt.invalid') from None
    return True


def new_report():
    return dict(success=False, browser_acceptance=False, http_denials_verified=False,
        complete_message_family_parity=False, versions=[], denial_versions=[], fixtures=[],
        errors=[], cleanup_errors=[], service_cleanup=dict.fromkeys(SERVICES, 'not_started'))


def run(args):
    # Output ownership precedes any preflight and never reuses/cleans an old output.
    output = s.fresh_output(args.output)
    report, secrets = new_report(), set()
    private, frozen, mask = None, None, None
    original, original_path = dict(os.environ), list(sys.path)
    def publish():
        path = output / 'results.json'
        s.browser.oracle.write_publishable(path, report, secrets)
        if report['success']:
            # Validate the bytes after redaction, JSON sorting and atomic replace,
            # not just the in-memory candidate or the writer's return value.
            validate_report(json.loads(path.read_bytes()))
    def escalation(control):
        invalidate(report, 'execution.cleanup-grace-expired', control, unverified=True)
        if private is not None:
            report.setdefault('recovery_directory', str(private))
            report['private_log_directory'] = str(private)
        bounded_publish(publish)
    control = ExecutionDeadline(args.deadline, args.cleanup_grace, on_escalation=escalation)
    write_failed = False
    try:
        with control:
            try:
                frozen = freeze_inputs(args)
                report.update(python=frozen['python'], input_binding=frozen,
                    native_build=frozen['native'], source_sha256_before=frozen['sources'],
                    parent_driver_sha256=frozen['sources'][OWNED[0]])
                # Do not relabel old build artifacts: the existing validators compare
                # their compiler inputs with this checkout, even for dirty sources.
                recheck_inputs(args, frozen)
                mask = os.umask(0o077)
                with tempfile.TemporaryDirectory(prefix='az-mutation-browser-', dir='/tmp') as directory:
                    private = Path(directory)
                    private.chmod(0o700)
                    execute_private(args, report, frozen, private, secrets, publish)
            except BaseException:
                invalidate(report, 'runtime.failed' if frozen is not None else 'preflight.failed')
            finally:
                control.begin_cleanup(preserve_deadline=True)
                os.environ.clear()
                os.environ.update(original)
                sys.path[:] = original_path
                report['environment_restored'] = dict(os.environ) == original
                if mask is not None:
                    os.umask(mask)
                report['umask_restored'] = True
                try:
                    if frozen is not None:
                        recheck_inputs(args, frozen)
                        report['source_sha256_after'] = source_hashes()
                        report['inputs_stable'] = report['source_sha256_after'] == frozen['sources']
                except BaseException:
                    report['cleanup_errors'].append('inputs.changed-or-unverified')
                report['private_directory_removed'] = private is not None and not private.exists()
                if report.get('owned_directory_removed') is True:
                    report.pop('recovery_directory', None)
                report['cleanup_verified'] = bool(report['private_directory_removed'] and
                    report['fixtures'] and all(row.get('verified') is True and row.get('login_restore') and
                    row.get('owned_logo_removed') is True for row in report['fixtures']) and
                    all(value == 'stopped' for value in report['service_cleanup'].values()) and
                    not report['cleanup_errors'])
                if control.interrupted:
                    invalidate(report, 'execution.interrupted', control)
                try:
                    report.update(success=True, browser_acceptance=True, http_denials_verified=True)
                    validate_report(report)
                except BaseException:
                    invalidate(report, 'receipt.invalid')
                try:
                    publish()
                except BaseException:
                    invalidate(report, 'receipt.write-failed')
                    write_failed = True
    except BaseException:
        invalidate(report, 'execution.failed', control)
        write_failed = True
    finally:
        # with control restores all handlers even when publication raises.
        # A signal in publication or __exit__ must invalidate the final receipt.
        if control.interrupted or write_failed:
            invalidate(report, 'execution.interrupted' if control.interrupted else 'receipt.write-failed', control)
            # The execution owner has restored its handlers. Give this final
            # invalidation a small I/O bound, never a second full cleanup grace.
            # The escalation callback adds at most two seconds for publication.
            final_budget = min(args.cleanup_grace, 2)
            with ExecutionDeadline(final_budget, final_budget, on_escalation=escalation) as final:
                final.begin_cleanup()
                # Remove a possible success candidate before a retry can stall.
                try:
                    (output / 'results.json').unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    publish()
                except BaseException:
                    write_failed = True
                    try:
                        (output / 'results.json').unlink(missing_ok=True)
                    except OSError:
                        pass
    return 0 if report['success'] and not write_failed else 1


def main(argv=None):
    try:
        return run(parse_args(argv))
    except Exception:
        print(json.dumps({'success': False, 'errors': ['cli.failed']}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
