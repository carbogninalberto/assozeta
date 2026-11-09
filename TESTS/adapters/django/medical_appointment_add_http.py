"""Parent-only subscription/<uid>/medical-appointments/add HTTP runner; imports are inert.

service_context owns a private CURRENT_HOST stub for the whole run (Django's
printing_tasks.CURRENT_HOST and Go's CURRENT_HOST point at it); it records
every request and answers with the case's scripted replies. A case's
'restore' row (instance.DataRestore) exists only while its request runs.

Seeds four subscriptions (own, own deleted, own grouped, foreign; audit
disabled). Each case is checked against the complete model state: at most
one created MedicalAppointments row with the authored stored values ('$now'
and '$default-date' within the request window), its auditlog create entry
(in-memory values: a defaulted date is str() of the aware datetime) with its
index row, and the exact outbound print requests (GET
/document/medical-appointment/<id>, requests' default headers, the request's
Authorization, no body).
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import ipaddress
import time
import types

from TESTS.contracts import test_medical_appointment_add_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import invoice_sync_print_http as printing
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/medical_appointment_add_http.json', 'TESTS/contracts/test_medical_appointment_add_http.py',
           'TESTS/adapters/django/medical_appointment_add_http.py', 'TESTS/adapters/django/invoice_sync_print_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/printing_tasks.py', 'BE/instance/restore/task_base.py',
           'BE/instance/restore/locking.py', 'BE/application/mixin.py', 'BE/application/tenant_scope.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py',
           'BE/application/utils/api_utils.py', 'BE/core/settings.py') + harness.SOURCES
APPOINTMENT = 'application.MedicalAppointments'
MANAGED = (APPOINTMENT, harness.AUDIT, harness.INDEX)
STUB = printing.STUB
service_context = printing.service_context


def _assert_case(case, base_url, *, token, sessions):
    with STUB['lock']:
        STUB['log'] = []
        STUB['script'] = [item['reply'] for item in case['prints']]
    STUB['token'] = token
    with printing._restore_row(case['restore']):
        return base_contract.assert_case(case, base_url, token=token, sessions=sessions)


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


def models():
    from application.models import MedicalAppointments, Subscription
    return MedicalAppointments, Subscription


def seed_rows(source, created):
    _, subscription = models()
    ids = source['ids']
    for row in source['subscriptions'].values():
        subscription._base_manager.bulk_create([subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=ids[row['user']], status_flag=row['status'],
            type=row['type'], role=row['role'], start_date=date.fromisoformat(row['start']), end_date=date.fromisoformat(row['end']),
            subscription_number=row['number'], group_id=ids[row['group']] if row['group'] else None, deleted=row['deleted'],
            creation_date=datetime(2026, 1, 10, 9, tzinfo=timezone.utc))])
        created.append((subscription, row['id']))


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(lambda: models()[0],))


def prepare(source):
    return {'started': time.time()}


def moment(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def check_prints(case, key):
    require = contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    expected = case['prints']
    require(len(log) == len(expected), 'print.request-count')
    headers = [('host', STUB['host']), ('user-agent', printing.USER_AGENT), ('accept-encoding', 'gzip, deflate'), ('accept', '*/*'),
               ('connection', 'keep-alive')]
    if STUB['token'] is not None:
        headers.append(('authorization', 'Bearer ' + STUB['token']))
    for index, (sent, spec) in enumerate(zip(log, expected)):
        where = f':{index}'
        require(sent['method'] == 'GET' and sent['path'] == spec['path'].replace('$id', str(key)), 'print.request-line' + where)
        require(sorted((name.lower(), value) for name, value in sent['headers']) == sorted(headers), 'print.headers' + where)
        require(sent['body'] == b'', 'print.body' + where)


def assert_effect(case, before, after, source, context):
    require = contract.require
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    ids, fixture = source['ids'], context['fixture']
    since, until = context['prepared']['started'], time.time()
    started, ended = datetime.fromtimestamp(since - 1, timezone.utc), datetime.fromtimestamp(until + 1, timezone.utc)
    old, new = keyed(before[APPOINTMENT], 'medical_appointments_id'), keyed(after[APPOINTMENT], 'medical_appointments_id')
    require(all(new.get(k) == v for k, v in old.items()), 'state.kept')
    fresh = [new[k] for k in set(new) - set(old)]
    added, indexes = harness.audit_delta(contract, before, after)
    effect = case['effect']
    if effect is None:
        require(not fresh and not added and not indexes, 'state.no-effect')
        check_prints(case, None)
        return
    require(len(fresh) == 1, 'state.created')
    row = {key: plain(value) for key, value in fresh[0].items()}
    key = str(row['medical_appointments_id'])
    check_prints(case, key)
    expected = effect['row']
    created = moment(row['creation_date'])
    if expected['creation_date'] == '$now':
        require(started <= created <= ended, 'state.creation-date-now')
    else:
        require(created == moment(expected['creation_date']), 'state.creation-date')
    audit_date = None
    if expected['date'] == '$default-date':
        audit_date = effect['changes']['date'][1]
        require(row['date'] == created.date().isoformat(), 'state.default-date')
    else:
        require(row['date'] == expected['date'], 'state.date')
    for column in ('region', 'notes', 'sport', 'address', 'meta'):
        require(row[column] == expected[column], 'state.' + column)
    require(str(row['subscription_id']) == expected['subscription'] and row['document_id'] is None, 'state.references')
    require(len(added) == 1 and len(indexes) == 1, 'audit.count')
    event = added[0]
    types_ = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    require(event['content_type_id'] == types_['medicalappointments'] and event['action'] == 0 and event['object_pk'] == key
            and event['object_repr'] == f'MedicalAppointments object ({key})', 'audit.identity')
    changes = {}
    for field, (left, right) in effect['changes'].items():
        right = {'$id': key, '$creation': str(created.astimezone(timezone.utc).replace(tzinfo=None))}.get(right, right)
        changes[field] = [left, right]
    if audit_date is not None:
        # str() of the aware default datetime: the stored date and within the request window.
        actual = event['changes'].get('date', [None, ''])[1]
        value = datetime.fromisoformat(actual)
        require(value.tzinfo is not None and started <= value <= ended and value.date().isoformat() == row['date']
                and actual == str(value), 'audit.default-date')
        changes['date'] = ['None', actual]
    require(event['changes'] == changes, 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    if effect['session']:
        additional = {'impersonation_session': fixture['sessions'][effect['session']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    require(index is not None and index['sport_association_id'] == effect['association']
            and index['resolution_path'] == 'subscription.sport_association', 'index.fields')


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
