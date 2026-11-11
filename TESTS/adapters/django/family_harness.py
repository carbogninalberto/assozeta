"""Shared parent-only Django runner harness for authored HTTP parity families; imports are inert.

A family adapter supplies its contract, a seed function and, for writes, an
effect check; this module provides the driver interface
(`fixture_context`, `run_workload`) with the same guarantees every family
runner has had:
- the fixture is seeded with auditlog disabled inside one transaction, and the
  complete model state, historical read cache and sequences are restored and
  verified afterwards (a cleanup receipt that is not verified fails the run);
- every case is attributed to exactly one request of the backend under test;
- a read family's cases must leave the complete model state unchanged;
- a write family's effect check sees the complete state before and after.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

AUDIT, INDEX = 'auditlog.LogEntry', 'application.AuditLogIndex'
SOURCES = ('TESTS/adapters/django/family_harness.py', 'TESTS/contracts/http_family.py')


def seed_extra_users(source, fixture):
    """Insert the source's family-specific athletes ('extra_users': {label: {id, username, email}}) with access tokens."""
    if not source.get('extra_users'):
        return
    from application.models import User
    from rest_framework_simplejwt.tokens import AccessToken
    fixture['tokens'] = dict(fixture['tokens'])
    for label, row in source['extra_users'].items():
        User._base_manager.bulk_create([User(user_id=row['id'], username=row['username'], email=row['email'],
                                             role=User.ATHLETE, password='!synthetic')])
        fixture['created'].append((User, row['id']))
        token = AccessToken()
        token['user_id'] = row['id']
        fixture['tokens'][label] = str(token)


def delete_unreadable(model, keep):
    """Delete the rows outside keep that hold a timestamp the driver cannot read.

    Django loads rows to delete them (signals, cascades), which fails for an aware
    value stored with a UTC instant before year 1 or after year 9999; those rows go
    by primary key, without loading them.
    """
    stamps = [field.attname for field in model._meta.concrete_fields if field.get_internal_type() == 'DateTimeField']
    if not stamps:
        return
    for pk in list(model._base_manager.exclude(pk__in=keep).values_list('pk', flat=True)):
        for name in stamps:
            try:
                model._base_manager.filter(pk=pk).values_list(name, flat=True).get()
            except (ValueError, OverflowError):
                model._base_manager.filter(pk=pk)._raw_delete(model._base_manager.db)
                break


def fixture_context_for(contract, seed, *, purge=()):
    """The driver's fixture_context.

    seed(source, created) inserts the family rows and appends (Model, pk) to
    created. purge: zero-argument callables returning models whose rows the run
    may create; rows that are neither original nor seeded are deleted before the
    final state comparison.
    """
    def seed_fixture(apps, private_work, raw):
        from auditlog.context import disable_auditlog
        from django.db import transaction
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        with disable_auditlog(), transaction.atomic():
            fixture = scopes.seed_fixture(apps, Path(private_work), raw)
            source = contract.load_source()
            seed_extra_users(source, fixture)
            seed(source, fixture['created'])
            fixture['baseline_state'] = scopes.snapshot_state(apps)
            fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
        return fixture

    @contextmanager
    def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        mutations.require_private_database()
        private_work = Path(private_work)
        contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
        before = scopes.snapshot_state(apps)
        original_audit = [row['id'] for row in before[AUDIT]]
        original = {model: list(model._base_manager.values_list('pk', flat=True)) for model in (load() for load in purge)}
        cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
        fixture = None
        receipt = cleanup_receipt if cleanup_receipt is not None else {}
        receipt.update(verified=False, results={})
        try:
            fixture = seed_fixture(apps, private_work, raw)
            yield fixture
        finally:
            def remove_rows():
                with disable_auditlog():
                    LogEntry.objects.exclude(pk__in=original_audit).delete()
                    if fixture is not None:
                        kept = {}
                        for model, keep in original.items():
                            # Only this model's seeded keys: pk types differ between models.
                            seeded = [pk for created, pk in fixture['created'] if created is model]
                            kept[model] = [*keep, *seeded]
                        # Rows the driver cannot read go first: deleting a parent loads its children.
                        for model, keys in kept.items():
                            delete_unreadable(model, keys)
                        for model, keys in kept.items():
                            model._base_manager.exclude(pk__in=keys).delete()
                        shared.delete_created(fixture)
                carnets.state_unchanged(before, scopes.snapshot_state(apps))
                return True
            receipt.update(scopes.cleanup_steps((
                ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
                ('sequences', lambda: mutations.restore_sequences(sequences_before)),
                ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
            )))
            contract.require(receipt['verified'], 'cleanup.fixture-incomplete')

    return fixture_context


def run_workload_for(contract, effect=None, *, prepare=None, handoff=None, throttle=None):
    """The driver's run_workload.

    effect(case, before, after, source, context) checks a write family's case;
    without it every case must leave the complete state unchanged. prepare(source)
    runs before each case and its result is context['prepared'] (for example
    rows captured before a delete). context['remote'] is shared across cases.
    throttle: the public_flow_throttle module when the family has routes open without an account: the address's
    request history is prepared before and checked after every case.
    handoff(case, backend) -> whether Go hands the case off to Django (handoff.md):
    such a case is attributed to one Django request, and the contract's
    assert_case also receives backend=.
    """
    def run_workload(label, backend, base_url, fixture, apps, raw, observer):
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        source = contract.load_source()
        carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
        results, context = [], {'remote': {}, 'fixture': fixture}
        ident = throttle.client_ident(base_url, contract.require) if throttle else None
        for case in source['cases']:
            if throttle:
                throttle.prepare(case, ident, contract.require)
            before = scopes.snapshot_state(apps) if effect else None
            context['prepared'] = prepare(source) if prepare else None
            cursor = observer.cursor()
            token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
            served = 'django' if handoff is not None and handoff(case, backend) else backend
            try:
                if handoff is None:
                    status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
                else:
                    status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], backend=backend)
                if throttle:
                    throttle.finish(case, status, ident, contract.require)
                if effect:
                    after = scopes.snapshot_state(apps)
                    shared.assert_attribution(served, case['method'], case['path'], status, observer.delta(cursor), case['name'])
                    effect(case, before, after, source, context)
                else:
                    shared.assert_attribution(served, case['method'], case['path'], status, observer.delta(cursor), case['name'])
                    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
            except BaseException as error:
                error.case = f'{label}:{case["name"]}'
                raise
            results.append({'case': case['name'], 'status': status})
        shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
        return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}

    return run_workload


def require_unmanaged_unchanged(contract, before, after, managed):
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed},
                     'state.other-models')


def audit_delta(contract, before, after):
    """New auditlog entries and their index rows; existing ones must be unchanged."""
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    contract.require(all(new_a[k] == old_a[k] for k in old_a) and all(new_i[k] == old_i[k] for k in old_i), 'audit.kept')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    return added, indexes


def check_audit_event(contract, case, event, indexes, *, before, ids, model, key, action, changes, actor, association, path, context):
    """One auditlog entry of the application model class `model` for `key`, with its index row."""
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor_id = ids[actor]
    contract.require(event['content_type_id'] == types[model.lower()] and event['action'] == action and event['object_pk'] == key
                     and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == actor_id and event['actor_email'] == users[actor_id]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes[str(event['id'])]
    contract.require(index['sport_association_id'] == association and index['resolution_path'] == path, 'index.fields')


def pairs(rows, left, right):
    """{(left, right)} of M2M through rows, as strings."""
    return {(str(scalar(row[left])), str(scalar(row[right]))) for row in rows}


# ---------------------------------------------------------------------------
# Celery publishes (docs/architecture/go-migration/celery-producer.md)
# ---------------------------------------------------------------------------

CELERY_QUEUE = 'celery'
CELERY_BINDING = ('_kombu.binding.' + CELERY_QUEUE, CELERY_QUEUE + '\x06\x16\x06\x16' + CELERY_QUEUE)
CELERY_HEADERS = ('lang', 'task', 'id', 'shadow', 'eta', 'expires', 'group', 'group_index', 'retries', 'timelimit', 'root_id',
                  'parent_id', 'argsrepr', 'kwargsrepr', 'origin', 'ignore_result', 'replaced_task_nesting', 'stamped_headers', 'stamps',
                  'assozeta_published_at')
CELERY_PROPERTIES = ('correlation_id', 'reply_to', 'delivery_mode', 'delivery_info', 'priority', 'body_encoding', 'delivery_tag')


def _reset_celery_app(app):
    """Drop the app's broker pools; kombu's global pool registry is keyed by
    connection, so it is reset as a whole (a closed pool would be reused)."""
    from kombu import pools
    pools.reset()
    app._pool = None
    app.__dict__.pop('amqp', None)


def with_celery_broker(fixture_context):
    """Wrap a family fixture_context so Django publishes Celery tasks like production.

    The test settings use the in-memory broker; for the fixture's lifetime the
    Django Celery app instead publishes to the run's private Redis on DB 0
    (CELERY_BROKER_URL has no database number) with the production queue
    settings (default queue 'celery', no routes) - the same broker the Go
    server's producer uses. fixture['celery_broker'] is a DB 0 client.
    """
    @contextmanager
    def wrapped(apps, private_work, raw, *, cleanup_receipt=None):
        import redis
        from core.celery import app
        options = raw.connection_pool.connection_kwargs
        broker = redis.Redis(host=options['host'], port=options['port'], db=0, password=options['password'],
                             socket_timeout=5, socket_connect_timeout=5)
        # The app reads Django settings with namespace='CELERY': override the prefixed keys.
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES')
        saved = {key: app.conf[key] for key in keys}
        url = f"redis://default:{options['password']}@{options['host']}:{options['port']}"
        try:
            broker.delete(CELERY_QUEUE)
            app.conf.update(dict(zip(keys, (url, CELERY_QUEUE, None))))
            assert app.conf.broker_url == url and app.conf.task_default_queue == CELERY_QUEUE, 'celery.broker-override'
            _reset_celery_app(app)
            with fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
                fixture['celery_broker'] = broker
                yield fixture
                leftover = broker.llen(CELERY_QUEUE)
                assert leftover == 0, 'celery.unchecked-messages'
        finally:
            app.conf.update(saved)
            _reset_celery_app(app)
            broker.delete(CELERY_QUEUE)
            broker.close()

    return wrapped


def drain_celery(fixture):
    """The raw messages on the broker queue, oldest first; the queue is emptied."""
    broker = fixture['celery_broker']
    with broker.pipeline(transaction=True) as pipe:
        pipe.lrange(CELERY_QUEUE, 0, -1)
        pipe.delete(CELERY_QUEUE)
        raw, _ = pipe.execute()
    return [item.decode('utf-8') for item in reversed(raw)]


def _celery_value(value):
    """Authored kwargs values: {'uuid': '<id>'} is a UUID; lists and pairs lists nest."""
    import uuid
    if isinstance(value, dict) and set(value) == {'uuid'}:
        return uuid.UUID(value['uuid'])
    if isinstance(value, list):
        return [_celery_value(item) for item in value]
    return value


def check_celery_message(contract, raw, spec, *, since, until, where=''):
    """One Redis payload against an authored task: the exact protocol 2 envelope
    (key order, constants, argsrepr/kwargsrepr, base64 JSON body bytes); only the
    task id, delivery tag, reply_to, origin and publish stamp vary, by shape."""
    import base64
    import json
    import re
    from celery.utils.saferepr import saferepr
    from kombu.utils.json import dumps, loads
    uuid_shape = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
    ordered = json.loads(raw, object_pairs_hook=lambda pairs: pairs)
    top = dict(ordered)
    contract.require([key for key, _ in ordered] == ['body', 'content-encoding', 'content-type', 'headers', 'properties'], 'celery.envelope' + where)
    contract.require(top['content-encoding'] == 'utf-8' and top['content-type'] == 'application/json', 'celery.content' + where)
    contract.require([key for key, _ in top['headers']] == list(CELERY_HEADERS), 'celery.header-order' + where)
    queue = spec.get('queue', CELERY_QUEUE)
    properties_order = list(CELERY_PROPERTIES)
    if spec.get('expires') is not None:
        properties_order.insert(3, 'expiration')
    contract.require([key for key, _ in top['properties']] == properties_order, 'celery.property-order' + where)
    message = json.loads(raw)
    headers, properties = message['headers'], message['properties']
    args = tuple(_celery_value(item) for item in spec['args'])
    kwargs = {key: _celery_value(item) for key, item in spec['kwargs']}
    task_id = headers['id']
    contract.require(isinstance(task_id, str) and uuid_shape.match(task_id) and task_id[14] == '4', 'celery.id' + where)
    # apply_async(task_id=...): the authored id.
    contract.require(spec.get('id', task_id) == task_id, 'celery.task-id' + where)
    eta = spec.get('eta')
    if spec.get('eta_from') is not None:
        # apply_async(countdown=target - now): eta is app.now() + countdown, the target plus the time between the
        # two clock reads (microseconds), rendered by isoformat() in UTC.
        from datetime import datetime, timedelta
        received = headers.get('eta')
        contract.require(isinstance(received, str) and received.endswith('+00:00'), 'celery.eta-shape' + where)
        moment, target = datetime.fromisoformat(received), datetime.fromisoformat(spec['eta_from'])
        contract.require(received == moment.isoformat() and target <= moment <= target + timedelta(seconds=5), 'celery.eta' + where)
        eta = received
    expected_headers = {
        'lang': 'py', 'task': spec['task'], 'id': task_id, 'shadow': None, 'eta': eta, 'expires': spec.get('expires'),
        'group': None, 'group_index': None, 'retries': 0, 'timelimit': spec.get('timelimit', [None, None]), 'root_id': task_id, 'parent_id': None,
        'argsrepr': saferepr(list(args) if spec.get('args_list') else args, 1024), 'kwargsrepr': saferepr(kwargs, 1024), 'origin': headers['origin'], 'ignore_result': False,
        'replaced_task_nesting': 0, 'stamped_headers': None, 'stamps': {}, 'assozeta_published_at': headers['assozeta_published_at']}
    contract.require(headers == expected_headers, 'celery.headers' + where)
    contract.require(re.fullmatch(r'gen\d+@\S+', headers['origin']) is not None, 'celery.origin' + where)
    stamp = headers['assozeta_published_at']
    contract.require(type(stamp) is float and since - 1 <= stamp <= until + 1, 'celery.published-at' + where)
    expiration = {'expiration': properties.get('expiration')} if 'expiration' in properties_order else {}
    contract.require(all(str(value).isdigit() for value in expiration.values()), 'celery.expiration' + where)
    contract.require(properties == {'correlation_id': task_id, 'reply_to': properties['reply_to'], 'delivery_mode': 2, **expiration,
                                    'delivery_info': {'exchange': '', 'routing_key': queue}, 'priority': 0, 'body_encoding': 'base64',
                                    'delivery_tag': properties['delivery_tag']}, 'celery.properties' + where)
    contract.require(uuid_shape.match(properties['reply_to']) is not None and uuid_shape.match(properties['delivery_tag']) is not None
                     and properties['delivery_tag'] != task_id, 'celery.property-shapes' + where)
    body = base64.b64decode(message['body'], validate=True)
    embed = {'callbacks': None, 'errbacks': None, 'chain': None, 'chord': None}
    contract.require(body == dumps([list(args), kwargs, embed]).encode('utf-8'), 'celery.body' + where)
    contract.require(loads(body) == [list(args), kwargs, embed], 'celery.body-decoded' + where)
    return message


def celery_published(contract, fixture, expected, *, since, until):
    """Effect check: the case published exactly `expected` (authored task specs,
    in order) to the default queue, and the queue's kombu binding is declared."""
    messages = drain_celery(fixture)
    contract.require(len(messages) == len(expected), 'celery.count')
    checked = [check_celery_message(contract, raw, spec, since=since, until=until, where=f':{index}')
               for index, (raw, spec) in enumerate(zip(messages, expected))]
    if expected:
        key, member = CELERY_BINDING
        contract.require({m.decode() for m in fixture['celery_broker'].smembers(key)} == {member}, 'celery.binding')
        fixture.setdefault('celery_accepted', []).extend(messages)
    return checked


def celery_worker_accepts(contract, fixture, payload, kwargs, *, restore_owner, association, timeout=30):
    """Execute one published message with a real worker of the Django Celery app.

    The raw payload is pushed back on the queue and consumed by
    celery.contrib.testing.worker (solo pool, the app's task registry,
    CoordinatedTask and the django-db result backend): the task must start
    with exactly `kwargs` (decoded values, e.g. UUIDs) and succeed. Then a
    data restore completed after the message's assozeta_published_at stamp is
    recorded and the same message must be refused by CoordinatedTask
    (StaleDataTask). Result and restore rows and the worker's broker keys are
    removed afterwards.
    """
    import json
    import time
    from auditlog.context import disable_auditlog
    from celery.contrib.testing.worker import start_worker
    from celery.signals import task_prerun
    from core.celery import app
    from django.utils import timezone
    from django_celery_results.models import TaskResult
    from instance.models import DataRestore
    broker = fixture['celery_broker']
    message = json.loads(payload)
    task_id, name = message['headers']['id'], message['headers']['task']
    started = []

    def prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **_):
        started.append((task_id, task.name, tuple(args or ()), dict(kwargs or {})))

    def wait(status):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = TaskResult.objects.filter(task_id=task_id, status=status).first()
            if row is not None:
                return row
            time.sleep(0.1)
        contract.require(False, 'celery.worker-timeout:' + status)

    restore = None
    task_prerun.connect(prerun, weak=False)
    try:
        with start_worker(app, pool='solo', concurrency=1, perform_ping_check=False, loglevel='WARNING', shutdown_timeout=30):
            broker.lpush(CELERY_QUEUE, payload)
            wait('SUCCESS')
            contract.require(started == [(task_id, name, (), kwargs)], 'celery.worker-arguments')
            with disable_auditlog():
                restore = DataRestore(owner_id=restore_owner, association_id=association, state='completed', completed_at=timezone.now(),
                                      sha256='0' * 64)
                contract.require(restore.completed_at.timestamp() > message['headers']['assozeta_published_at'], 'celery.restore-after-publish')
                DataRestore._base_manager.bulk_create([restore])
            broker.lpush(CELERY_QUEUE, payload)
            refused = wait('FAILURE')
            contract.require('StaleDataTask' in (refused.result or ''), 'celery.worker-restore-fence')
    finally:
        task_prerun.disconnect(prerun)
        with disable_auditlog():
            TaskResult.objects.filter(task_id=task_id).delete()
            if restore is not None:
                DataRestore._base_manager.filter(pk=restore.pk).delete()
        key, _ = CELERY_BINDING
        for leftover in broker.keys('*'):
            if leftover.decode() != key:
                broker.delete(leftover)
    contract.require(broker.llen(CELERY_QUEUE) == 0, 'celery.worker-drained')
    return {'task_id': task_id, 'executed': True, 'restore_fence': True}
