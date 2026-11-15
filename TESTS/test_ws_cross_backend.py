"""Cross-backend realtime WebSocket matrix (Django and Go holders, three origins).

For each socket holder (Django Channels under uvicorn, the native Go server) and
each event origin (Django's configured channel layer, Go's ``channels.GroupSend``
and a relayed ``assozeta_go_realtime_events`` row through both relay
implementations), one fresh ``ws/notifications/`` socket receives exactly one
text frame. The Django-holder frame is the authored reference: it must equal
``json.dumps`` of what ``NotificationConsumer`` sends, and every Go-holder frame
must equal that same reference byte for byte. A quiet period after each frame
proves there is no duplicate.

Both relay implementations allowlist only ``staff_board_changed`` (Django's
``go_realtime.HANDLERS`` and Go's ``relayGoRealtimeHandlers``), so the outbox
origin covers that event only; ``notification_push`` and ``export_progress``
are exercised through the two direct ``group_send`` origins.

Run with the production interpreter from the repository root:

    python TESTS/test_ws_cross_backend.py [--output DIR]

It starts one private Redis, a private (or shared) PostgreSQL database, Django's
real ASGI application under uvicorn with the shipped channel layer, and the
native Go server on the same Redis. No repository file is written; only
``results.json`` is published under the output directory.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from TESTS import ws_client

HOLDERS = ('django', 'go')
DIRECT_EVENTS = ('notification_push', 'staff_board_changed', 'export_progress')
QUIET_SECONDS = 1.0
TIMEOUT = 15


class MatrixFailure(AssertionError):
    pass


def require(condition, check):
    if not condition:
        raise MatrixFailure(check)


# ---------------------------------------------------------------------------
# One WebSocket session
# ---------------------------------------------------------------------------

def endpoint(base_url):
    from urllib.parse import urlsplit
    url = urlsplit(base_url)
    return url.hostname, url.port


def connect(base_url, token):
    host, port = endpoint(base_url)
    session = ws_client.Session(host, port, timeout=TIMEOUT)
    path = '/ws/notifications/?token=' + token
    try:
        handshake = session.open(ws_client.build_request(path, f'{host}:{port}'))
        require(handshake.status == 101, f'handshake.status:{handshake.status}')
    except BaseException:
        session.close()
        raise
    return session


def receive_text(session):
    event = session.recv(TIMEOUT)
    require('text' in event, f'event.not-text:{json.dumps(event)[:200]}')
    return event['text']


def assert_no_duplicate(session):
    try:
        event = session.recv(QUIET_SECONDS)
    except TimeoutError:
        return
    raise MatrixFailure(f'duplicate-or-extra:{json.dumps(event)[:300]}')


# ---------------------------------------------------------------------------
# Events and groups
# ---------------------------------------------------------------------------

def event_specs(association, owner_id, notification_id):
    notification = {
        'id': notification_id,
        'type': 'subscription',
        'read': False,
        'msg': 'Nuova iscrizione — è pronta',
        'association_id': association,
        'issued': 1762685000,
        'date': 'Sun Nov  9 10:43:20 2026',
    }
    return {
        'notification_push': {
            'group': f'notifications_user_{owner_id}',
            'message': {'type': 'notification_push', 'notification': notification},
            'expected': {'type': 'notification_push', 'notification': notification},
        },
        'staff_board_changed': {
            'group': f'staff_board_{association}',
            'message': {'type': 'staff_board_changed'},
            'expected': {'type': 'staff_board_changed'},
        },
        'export_progress': {
            'group': f'notifications_user_{owner_id}',
            'message': {'type': 'export_progress',
                        'payload': {'progress': 42, 'state': 'running', 'message': 'Esportazione — in corso'}},
            'expected': {'type': 'export_progress', 'progress': 42, 'state': 'running',
                         'message': 'Esportazione — in corso'},
        },
    }


# ---------------------------------------------------------------------------
# Origins
# ---------------------------------------------------------------------------

def django_layer_send(spec):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    async_to_sync(get_channel_layer().group_send)(spec['group'], spec['message'])
    return None


def go_channels_send(spec, work, go_test_environment):
    message_path = work / f'message-{uuid.uuid4().hex}.json'
    message_path.write_text(json.dumps(spec['message']))
    try:
        environment = {**go_test_environment,
                       'WS_CROSS_BACKEND_GROUP_SEND': '1',
                       'WS_CROSS_BACKEND_GROUP': spec['group'],
                       'WS_CROSS_BACKEND_MESSAGE_FILE': str(message_path),
                       'WS_CROSS_BACKEND_REDIS_DB': '0'}
        completed = subprocess.run(
            ['go', 'test', './internal/channels', '-run', '^TestCrossBackendGroupSend$', '-count=1', '-v'],
            cwd=ROOT / 'BE_GO', env=environment, capture_output=True, text=True, timeout=300)
        require(completed.returncode == 0,
                f'go-group-send.exit:{completed.returncode}:{completed.stderr[-500:]}')
        require('--- PASS: TestCrossBackendGroupSend' in completed.stdout,
                f'go-group-send.not-run:{completed.stdout[-500:]}')
    finally:
        message_path.unlink(missing_ok=True)
    return None


def insert_outbox(target):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO public.assozeta_go_realtime_events(envelope_version, kind, target_id) '
            'VALUES (1, %s, %s) RETURNING id', ['staff_board_changed', target])
        return cursor.fetchone()[0]


def outbox_state(identifier):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT attempts, delivered_at, failed_code '
                       'FROM public.assozeta_go_realtime_events WHERE id = %s', [identifier])
        return cursor.fetchone()


def delete_outbox(identifier):
    from TESTS.adapters.django.websockets_http import delete_outbox as remove
    remove(identifier)


def django_relay_send(target):
    from notifications.go_realtime import relay_once
    identifier = insert_outbox(target)
    try:
        counts = relay_once()
        require(counts == {'delivered': 1, 'retry': 0, 'failed': 0}, f'django-relay.counts:{counts}')
        row = outbox_state(identifier)
        require(row is not None and row[0] == 1 and row[1] is not None and row[2] is None,
                f'django-relay.outbox:{row}')
    finally:
        delete_outbox(identifier)
    return None


def go_relay_send(target, executable):
    identifier = insert_outbox(target)
    try:
        completed = subprocess.run([str(executable), '--once'], env=dict(os.environ),
                                   capture_output=True, text=True, timeout=180)
        require(completed.returncode == 0,
                f'go-relay.exit:{completed.returncode}:{completed.stderr[-500:]}')
        require(completed.stdout.strip() == "{'delivered': 1, 'retry': 0, 'failed': 0}",
                f'go-relay.stdout:{completed.stdout[-300:]!r}')
        row = outbox_state(identifier)
        require(row is not None and row[0] == 1 and row[1] is not None and row[2] is None,
                f'go-relay.outbox:{row}')
    finally:
        delete_outbox(identifier)
    return None


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def go_tool_environment():
    """Keep the provisioned toolchain on PATH; never read ambient Go config."""
    from TESTS.adapters.django import message_reads_runtime as reuse
    tools = reuse.sanitized_tool_environment()
    tools.setdefault('PATH', os.defpath)
    toolchain = Path(os.environ.get(
        'GO_MIGRATION_GO_BIN',
        Path.home() / 'go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin'))
    if toolchain.is_dir():
        tools['PATH'] = f'{toolchain}:{tools["PATH"]}'
    tools.update({'LC_ALL': 'C', 'LANG': 'C', 'GOPROXY': 'off', 'GOTOOLCHAIN': 'local',
                  'GOSUMDB': 'off', 'GOFLAGS': '-mod=readonly'})
    return tools


def build_go_relay(work, log):
    executable = work / 'relay-go-realtime'
    completed = subprocess.run(
        ['go', 'build', '-o', str(executable), './cmd/relay-go-realtime'],
        cwd=ROOT / 'BE_GO', env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT, timeout=300)
    require(completed.returncode == 0, 'go-relay.build')
    return executable


@contextlib.contextmanager
def mirrored_redis_layer(redis_port, password):
    """The shipped cross-runtime layer on the private Redis both servers share.

    TESTS.adapters.django.settings replaces CHANNEL_LAYERS with Channels'
    InMemoryChannelLayer for HTTP-only families. The WebSocket matrix needs
    GoMirroredRedisChannelLayer on database 0, exactly as BE/core/settings.py
    configures it in production and as the Go server subscribes.
    """
    from django.test.utils import override_settings
    layers = {'default': {
        'BACKEND': 'notifications.realtime_layer.GoMirroredRedisChannelLayer',
        'CONFIG': {'hosts': [{
            'address': f'redis://127.0.0.1:{redis_port}/0', 'password': password}]},
    }}
    with override_settings(CHANNEL_LAYERS=layers):
        yield


def source_digest():
    """Bind the published cells to the exact harness and helper sources."""
    names = ('TESTS/test_ws_cross_backend.py', 'TESTS/ws_client.py',
             'BE_GO/internal/channels/crossbackend_helper_test.go',
             'BE_GO/internal/channels/channels.go', 'BE_GO/internal/service/realtime_hub.go',
             'BE_GO/internal/repository/notification_store.go',
             'BE/notifications/realtime_layer.py', 'BE/notifications/consumers.py')
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

def run_matrix(output):
    from TESTS.adapters.django.production_runtime import require_production_python
    from TESTS.adapters.django import message_reads_runtime as reuse
    from TESTS.adapters.django import message_read_scopes_runtime as scopes_runtime
    from TESTS.adapters.django import shared_postgres
    from TESTS.adapters.django import websockets_http
    from TESTS.adapters.django.message_mutations_acceptance import _owned, configure_database_bounds, PG_BOUNDS
    from TESTS.contracts import test_websockets_http as contract

    result = {'success': False, 'scope': __doc__.strip(), 'cells': [], 'errors': []}
    result['python'] = require_production_python()
    result['source_sha256'] = source_digest()
    tools = go_tool_environment()
    postgres_settings = shared_postgres.resolve(os.environ)
    shared_postgres.activate(postgres_settings)
    result['postgres_mode'] = 'shared' if postgres_settings.postgres else 'private'
    shared = None
    if postgres_settings.postgres:
        import django
        shared = (postgres_settings.postgres, shared_postgres.template_key(
            ROOT, extra=f'django={django.get_version()};python={platform.python_version()}'))

    output.mkdir(parents=True, exist_ok=True)
    runtime_report = {'service_cleanup': {}, 'fixture_cleanup': []}
    with tempfile.TemporaryDirectory(prefix='az-ws-cross-', dir='/tmp') as directory, \
            (Path(directory) / 'private.log').open('w') as log:
        private = Path(directory)
        try:
            with _owned(reuse.owned_message_database(private, runtime_report, shared=shared)) as (work, redis_port, _services):
                env, _private = reuse.private_environment(work, redis_port, tools)
                env.update(PG_BOUNDS)
                reuse.apply_private_environment(env)
                raw = override = None
                try:
                    apps, _settings = reuse.initialize_django_apps()
                    from django.db import connections
                    from django.test.utils import override_settings
                    configure_database_bounds(connections)
                    result['schema'] = reuse.migrate_full_schema(log)
                    if shared is not None and runtime_report.get('shared_postgres', {}).get('from_template') is False:
                        from TESTS.adapters.django.historical_read_routes import database_target
                        connections.close_all()
                        _host, name = database_target(work)
                        runtime_report['shared_postgres']['template_published'] = \
                            shared_postgres.publish_template(shared[0], name, shared[1])
                    from TESTS.adapters.go import go_operational_migrations
                    result['go_operational_schema'] = go_operational_migrations.apply(work, {**env, **tools}, log)

                    override = override_settings(CACHES=reuse.cache_override(redis_port))
                    override.enable()
                    import redis
                    from TESTS.http_client import poll
                    raw = redis.Redis(host='127.0.0.1', port=redis_port, db=1,
                                      password=env['REDIS_PASSWORD'], decode_responses=False,
                                      socket_timeout=5, socket_connect_timeout=5)
                    poll(raw.ping, bool, timeout=15)

                    application = websockets_http.asgi_application()
                    with mirrored_redis_layer(redis_port, env['REDIS_PASSWORD']), \
                            _owned(reuse.django_application(runtime_report, False, None, application=application)) as \
                            (django_url, _observer, _http_port):
                        with _owned(scopes_runtime.go_service(reuse, runtime_report, work, log, tools,
                                                              redis_port, False)) as native:
                            go_url = native['base_url']
                            bases = {'django': django_url, 'go': go_url}
                            result['bases'] = {'django': 'native', 'go': 'native'}

                            fixture_path = private / 'fixture-ws-cross'
                            cleanup = {}
                            runtime_report.setdefault('fixture_cleanup', []).append(cleanup)
                            with websockets_http.fixture_context(apps, fixture_path, raw,
                                                                 cleanup_receipt=cleanup) as fixture:
                                source = contract.load_source()
                                owner_id = source['ids']['owner']
                                from application.models import User
                                from communications.staff_board import readable_association_id
                                owner = User._base_manager.get(pk=owner_id)
                                association = readable_association_id(owner.pk)
                                require(association == source['ids']['own_association'],
                                        f'fixture.association:{association}')
                                token = fixture['ws_tokens']['owner']
                                specs = event_specs(association, owner_id, source['ids']['newest_email'])

                                relay_executable = None
                                try:
                                    relay_executable = build_go_relay(work, log)
                                except BaseException as error:
                                    result['errors'].append({'stage': 'go-relay-build', 'error': type(error).__name__})
                                    traceback.print_exc(file=log)

                                origins = [
                                    ('django-layer', DIRECT_EVENTS,
                                     lambda spec: django_layer_send(spec)),
                                    ('go-channels', DIRECT_EVENTS,
                                     lambda spec: go_channels_send(spec, work, dict(os.environ))),
                                    # Both relays allowlist only staff_board_changed.
                                    ('go-outbox-django-relay', ('staff_board_changed',),
                                     lambda spec: django_relay_send(association)),
                                ]
                                if relay_executable is not None:
                                    origins.append(
                                        ('go-outbox-go-relay', ('staff_board_changed',),
                                         lambda spec: go_relay_send(association, relay_executable)))

                                references = {}
                                for origin, events, produce in origins:
                                    for event_name in events:
                                        spec = specs[event_name]
                                        expected = json.dumps(spec['expected']).encode('utf-8')
                                        references.pop((origin, event_name), None)
                                        for holder in HOLDERS:
                                            cell = {'holder': holder, 'origin': origin, 'event': event_name}
                                            session = None
                                            try:
                                                session = connect(bases[holder], token)
                                                produce(spec)
                                                text = receive_text(session)
                                                assert_no_duplicate(session)
                                                payload = text.encode('utf-8')
                                                if holder == 'django':
                                                    require(payload == expected,
                                                            f'django-text:{text[:300]!r}:expected:{expected[:300]!r}')
                                                    references[(origin, event_name)] = payload
                                                else:
                                                    reference = references.get((origin, event_name))
                                                    require(reference is not None, 'reference-unavailable')
                                                    require(payload == reference,
                                                            f'go-text:{text[:300]!r}:reference:{reference[:300]!r}')
                                                cell.update(status='pass',
                                                            text_sha256=hashlib.sha256(payload).hexdigest())
                                            except BaseException as error:
                                                cell.update(status='fail', detail=str(error)[:800])
                                                if holder == 'django':
                                                    references.pop((origin, event_name), None)
                                                if not isinstance(error, MatrixFailure):
                                                    traceback.print_exc(file=log)
                                            finally:
                                                if session is not None:
                                                    session.close()
                                                result['cells'].append(cell)
                                                print(f"{cell['status'].upper()}: {holder} <- {origin} / {event_name}",
                                                      flush=True)
                                result['fixture_cleanup'] = runtime_report.get('fixture_cleanup')
                            require(cleanup.get('verified') is True, 'fixture.cleanup')
                finally:
                    for name, operation in (
                            ('redis_client', lambda: raw.close() if raw is not None else None),
                            ('db_connections', lambda: __import__('django.db').db.connections.close_all()),
                            ('cache_override', lambda: override.disable() if override is not None else None)):
                        try:
                            operation()
                        except BaseException:
                            result['errors'].append({'stage': name})
                            traceback.print_exc(file=log)
            result['services'] = runtime_report.get('service_cleanup', {})
            result['shared_postgres'] = runtime_report.get('shared_postgres')
        except BaseException as error:
            result['errors'].append({'stage': 'matrix', 'type': type(error).__name__,
                                     'detail': str(error)[:400]})
            traceback.print_exc(file=log)

    passed = sum(cell['status'] == 'pass' for cell in result['cells'])
    result['summary'] = {'total': len(result['cells']), 'passed': passed,
                         'failed': len(result['cells']) - passed}
    result['success'] = bool(result['cells']) and passed == len(result['cells']) and not result['errors']
    (output / 'results.json').write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + '\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'quality-reports/go-migration/ws-cross-backend')
    args = parser.parse_args(argv)
    result = run_matrix(args.output)
    print(json.dumps(result['summary']))
    for cell in result['cells']:
        if cell['status'] != 'pass':
            print(f"FAILED: {cell}", file=sys.stderr)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
