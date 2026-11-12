"""Parent-only subscription/<uid>/approve HTTP runner; imports are inert.

Seeds associates and subscriptions (audit disabled) and verifies each case
against the complete model state: exactly the authored subscription columns
change (acceptance_date is the UTC date of the request's timezone.now()), one
auditlog entry with its index row is appended per approval (the acceptance
datetime rendered as str(datetime) within the request window), and the Celery
messages on the run's private broker are exactly the authored ones: one
check_workflows_trigger message per approval, none otherwise. After every
endpoint's sequence the last published message is executed by a real worker
of the Django Celery app, and refused by CoordinatedTask once a data restore
has completed after its publish stamp.
"""
from datetime import date, datetime, timezone
import ipaddress
import re
import time
import uuid

from TESTS.contracts import test_subscription_approve_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_approve_http.json', 'TESTS/contracts/test_subscription_approve_http.py',
           'TESTS/adapters/django/subscription_approve_http.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py', 'BE/application/signals.py',
           'BE/application/tasks.py', 'BE/core/celery.py', 'BE/core/settings.py', 'BE/instance/restore/task_base.py',
           'BE/application/mixin.py', 'BE/application/permissions.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py', 'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py') + harness.SOURCES
SUB = 'application.Subscription'
MANAGED = (SUB, harness.AUDIT, harness.INDEX)
NOW = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{6})?\+00:00\Z')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import Associate, Subscription
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'],
                                    last_name=row['last'], sex='M', is_minor=False))
    for row in source['subscriptions'].values():
        owner = ids['owner'] if row['association'] == ids['own_association'] else ids['foreign_owner']
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
            associate_id=source['associates'][row['associate']]['id'] if row['associate'] else None, status_flag=row['status'],
            acceptance_date=day(row['acceptance']), start_date=day(row['start']), end_date=day(row['end']),
            creation_date=moment(row['created']), group_id=ids[row['group']] if row['group'] else None, archived=row['archived'],
            deleted=row['deleted'], signature_storage_key=row['signature_key'], signature_url=row['signature_url']))


fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    if isinstance(value, date):
        return value.isoformat()
    return value


def flat(row):
    return {key: plain(value) for key, value in row.items()}


def prepare(source):
    return {'started': time.time()}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    since, until = context['prepared']['started'], time.time()
    effect = case['effect']
    harness.celery_published(contract, fixture, effect['celery'] if effect else [], since=since, until=until)
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    row = source['subscriptions'][effect['subscription']]
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == 1 and len(indexes) == 1, 'audit.count')
    event = added[0]
    accepted = event['changes'].get('acceptance_date', [None, ''])[1]
    contract.require(NOW.match(accepted or '') is not None, 'audit.acceptance-format')
    accepted_at = datetime.fromisoformat(accepted)
    contract.require(since - 1 <= accepted_at.timestamp() <= until + 1, 'audit.acceptance-window')
    bindings = {'$now': accepted, '$today': accepted_at.astimezone(timezone.utc).date().isoformat()}
    columns = {key: bindings.get(value, value) for key, value in effect['columns'].items()}
    expected = []
    for stored in before[SUB]:
        stored = flat(stored)
        if str(stored['subscription_id']) == row['id']:
            stored = {**stored, **columns}
        expected.append(stored)
    contract.require([flat(r) for r in after[SUB]] == expected, 'state.subscriptions')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    contract.require(event['content_type_id'] == types['subscription'] and event['action'] == 1 and event['object_pk'] == row['id']
                     and event['object_repr'] == f"Subscription object ({row['id']})", 'audit.identity')
    changes = {key: [bindings.get(v, v) for v in pair] for key, pair in effect['audit'].items()}
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    contract.require(index is not None and index['sport_association_id'] == effect['index'][0]
                     and index['resolution_path'] == effect['index'][1], 'index.fields')


base_run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    result = base_run_workload(label, backend, base_url, fixture, apps, raw, observer)
    source = contract.load_source()
    published = fixture.get('celery_accepted') or []
    contract.require(len(published) == sum(1 for case in source['cases'] if case['effect']), 'celery.published-total')
    # The last approval: its message must be executable by the Django worker.
    last = [case for case in source['cases'] if case['effect']][-1]['effect']
    row = source['subscriptions'][last['subscription']]
    kwargs = {'trigger_type': 'subscription_approved', 'sport_association_id': uuid.UUID(row['association']),
              'subscription_id': uuid.UUID(row['id'])}
    try:
        result['celery_worker'] = harness.celery_worker_accepts(contract, fixture, published[-1], kwargs,
                                                                restore_owner=source['ids']['owner'], association=row['association'])
    except BaseException as error:
        error.case = f'{label}:celery-worker'
        raise
    return result

