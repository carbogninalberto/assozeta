"""Parent-only authored HTTP acceptance on native Django/Go and Caddy v1/v2.

Run with the production interpreter:
``python -m TESTS.adapters.django.staff_board_http_acceptance --output NEW_DIRECTORY [--versioned]
--family FAMILY``
(for example staff-board, email-logs, workflows).
Reuses the proven private PostgreSQL/Redis, Django ASGI observer, native Go
server and shipped Caddy lifetimes. Each endpoint gets a fresh fixture and a
verified cleanup receipt. Only the credential-free results.json is published.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
# Each family is TESTS.adapters.django.<family>_http and declares its own SOURCES,
# so adding a family never changes this driver or other families' bindings.
def family_runner(family):
    import importlib
    import re
    assert re.fullmatch(r'[a-z]+(-[a-z]+)*', family), 'family.name'
    runner = importlib.import_module('TESTS.adapters.django.' + family.replace('-', '_') + '_http')
    assert all(callable(getattr(runner, name, None)) for name in ('fixture_context', 'run_workload')) \
        and isinstance(getattr(runner, 'SOURCES', None), tuple), 'family.interface'
    return runner


SOURCES = ('TESTS/adapters/django/staff_board_http_acceptance.py', 'BE_GO/cmd/server/main.go',
           'TESTS/adapters/go/go_operational_migrations.py', 'BE_GO/migrations/migrations.go',
           'BE_GO/migrations/000004_realtime_events.up.sql')


def source_hashes(family='staff-board'):
    names = SOURCES + tuple(family_runner(family).SOURCES)
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def execute(report, tools, private, log):
    from TESTS.adapters.django import message_reads_runtime as reuse
    from TESTS.adapters.django.message_read_scopes_runtime import go_service, caddy_liveness_guard, LIFECYCLE_GUARD
    from TESTS.adapters.django.message_mutations_acceptance import _owned, configure_database_bounds, PG_BOUNDS
    runner = family_runner(report['family'])
    versioned = report['versioned']
    with _owned(reuse.owned_message_database(private, report)) as (work, port, _services):
        env, _ = reuse.private_environment(work, port, tools)
        env.update(PG_BOUNDS)
        reuse.apply_private_environment(env)
        raw, override = None, None
        try:
            apps, _ = reuse.initialize_django_apps()
            from django.db import connections
            from django.test.utils import override_settings
            from TESTS.http_client import poll, Client
            import redis
            configure_database_bounds(connections)
            override = override_settings(CACHES=reuse.cache_override(port))
            override.enable()
            report['schema'] = reuse.migrate_full_schema(log)
            from TESTS.adapters.go import go_operational_migrations
            report['go_operational_schema'] = go_operational_migrations.apply(work, {**env, **tools}, log)
            raw = redis.Redis(host='127.0.0.1', port=port, db=1, password=env['REDIS_PASSWORD'],
                              decode_responses=False, socket_timeout=5, socket_connect_timeout=5)
            poll(raw.ping, bool, timeout=15)
            guards = []
            with _owned(reuse.django_application(report, versioned, None, liveness_observer=guards.append)) as (url, observer, http_port):
                with _owned(go_service(reuse, report, work, log, tools, port, versioned)) as native:
                    endpoints = [('django-native', 'django', url, guards[0]),
                                 ('go-native', 'go', native['base_url'], native[LIFECYCLE_GUARD])]

                    def run(selected):
                        for index, (label, backend, base_url, guard) in enumerate(selected):
                            cleanup = {}
                            report['fixture_cleanup'].append(cleanup)
                            with runner.fixture_context(apps, private / f'fixture-{label}', raw, cleanup_receipt=cleanup) as fixture:
                                workload = runner.run_workload(label, backend, base_url, fixture, apps, raw, observer)
                                guard()
                                report['workloads'].append(workload)

                    if versioned:
                        upstream = f'host.docker.internal:{http_port}'
                        with _owned(reuse.owned_caddy(report, log, upstream, native['proxy_upstream'], pull_policy='never')) as (gateway, _image, container):
                            status, _, _headers = Client(gateway).request('GET', '/healthz', include_headers=True)
                            assert status == 200, 'proxy-health'
                            report['caddy_image_sha256'] = container['image_id'].removeprefix('sha256:')
                            endpoints += [
                                ('caddy-v1', 'django', gateway + '/api/v1', caddy_liveness_guard(guards[0], container['container_id'])),
                                ('caddy-v2', 'go', gateway + '/api/v2', caddy_liveness_guard(native[LIFECYCLE_GUARD], container['container_id']))]
                            run(endpoints)
                    else:
                        run(endpoints)
            report['runtime_executed'] = True
        finally:
            for name, operation in (('redis_client', lambda: raw.close() if raw is not None else None),
                                    ('db_connections', lambda: __import__('django.db').db.connections.close_all()),
                                    ('cache_override', lambda: override.disable() if override is not None else None)):
                try:
                    operation()
                    report['resource_cleanup'][name] = True
                except BaseException:
                    report['resource_cleanup'][name] = False
                    traceback.print_exc(file=log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--versioned', action='store_true')
    parser.add_argument('--family', required=True)
    args = parser.parse_args(argv)
    from TESTS.adapters.django import message_reads_runtime as reuse
    output = reuse.prepare_output_directory(args.output)
    report = {'success': False, 'scope': args.family + ' authored HTTP sequence', 'versioned': args.versioned, 'family': args.family,
              'complete_family_parity': False, 'workloads': [], 'fixture_cleanup': [], 'resource_cleanup': {},
              'service_cleanup': {}, 'errors': [], 'runtime_executed': False}
    before = None
    try:
        from TESTS.adapters.django.production_runtime import require_production_python
        report['python'] = require_production_python()
        before = source_hashes(args.family)
        report['source_sha256'] = before
        tools = reuse.sanitized_tool_environment()
        # Django and the Go server share the host clock; window fixtures keep
        # wide margins so any host offset below them yields the same result.
        tools.update({'LC_ALL': 'C', 'LANG': 'C', 'GOPROXY': 'off', 'GOTOOLCHAIN': 'local',
                      'GOSUMDB': 'off', 'GOFLAGS': '-mod=readonly'})
        with patch.dict(os.environ, tools, clear=True):
            with tempfile.TemporaryDirectory(prefix='az-staff-board-http-', dir='/tmp') as directory:
                private = Path(directory)
                with (private / 'private.log').open('w') as log:
                    try:
                        execute(report, tools, private, log)
                    except BaseException as error:
                        # Diagnostics stay private; publish only the fixed failure location.
                        traceback.print_exc(file=log)
                        log.flush()
                        report['errors'].append({'type': type(error).__name__, 'case': getattr(error, 'case', None),
                                                 'check': str(error)[:120] if isinstance(error, AssertionError) else None,
                                                 'actual_status': getattr(error, 'actual_status', None)})
                        (output / 'failure.log').write_text((private / 'private.log').read_text()[-20000:])
                        (output / 'failure.log').chmod(0o600)
            report['private_output_removed'] = not Path(directory).exists()
    except BaseException as error:
        report['errors'].append({'type': type(error).__name__})
    finally:
        report['source_stable'] = before is not None and source_hashes(args.family) == before
        report['fixture_cleanup_verified'] = bool(report['fixture_cleanup']) and all(r.get('verified') for r in report['fixture_cleanup'])
        report['services_stopped'] = all(state in ('stopped', None) for state in report['service_cleanup'].values()) \
            if report['service_cleanup'] else False
        expected = 4 if args.versioned else 2
        report['success'] = bool(not report['errors'] and report['runtime_executed'] and report['source_stable']
                                 and report['fixture_cleanup_verified'] and len(report['workloads']) == expected
                                 and all(w['success'] for w in report['workloads']) and report.get('private_output_removed'))
        (output / 'results.json').write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + '\n')
    print(json.dumps({'success': report['success'], 'workloads': [w['label'] for w in report['workloads']],
                      'errors': report['errors']}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
