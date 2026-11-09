"""Parent-only subscription/<uid>/update and medical-certificate/send-email-reminder HTTP runner; imports are inert.

Seeds documents, associates, tutor relations, certificates and subscriptions
(audit disabled) and verifies each case against the complete model state:
an update changes exactly the authored subscription columns and appends at most
one auditlog entry with its index row (a kept entry: the entry is committed and
the row is unchanged); a reminder changes nothing. The Celery messages on the
run's private broker are exactly the authored ones: print_document_subscription
with the request's Authorization header after each saved update, and
send_email_template after each reminder, none otherwise. '$authorization',
'$email:<actor>', '$denomination:<own|foreign>', '$app_url', '$whitelabel' and
'$is_whitelabel' are resolved from the request, the seeded rows and Django's
settings.
"""
from datetime import date, datetime
import ipaddress
import time

from TESTS.contracts import test_celery_routes_a_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/celery_routes_a_http.json', 'TESTS/contracts/test_celery_routes_a_http.py',
           'TESTS/adapters/django/celery_routes_a_http.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/services/subscription_service.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/utils/api_utils.py', 'BE/application/tasks.py',
           'BE/core/celery.py', 'BE/core/settings.py', 'BE/instance/restore/task_base.py', 'BE/application/mixin.py',
           'BE/application/permissions.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/permissions_registry.py') + harness.SOURCES
SUB = 'application.Subscription'
MANAGED = (SUB, harness.AUDIT, harness.INDEX)


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import Associate, AssociateTutorRelation, Document, MedicalCertificate, Subscription
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['documents'].values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'], creation_date=moment('2026-01-05T08:00:00Z')))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'],
                                    last_name=row['last'], email=row['email'], sex='M', is_minor=False))
    for associate, tutor, primary in source['relations']:
        insert(AssociateTutorRelation, AssociateTutorRelation(associate_id=source['associates'][associate]['id'],
                                                              tutor_id=source['associates'][tutor]['id'], is_primary=primary))
    for row in source['certificates'].values():
        insert(MedicalCertificate, MedicalCertificate(medical_id=row['id'], user_id=ids[row['user']], expiration_date=day(row['expiration']),
                                                      creation_date=moment('2026-01-05T08:00:00Z')))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=ids[row['user']],
            associate_id=source['associates'][row['associate']]['id'] if row['associate'] else None, status_flag=row['status'],
            type=row['type'], role=row['role'], start_date=day(row['start']), end_date=day(row['end']),
            acceptance_date=day(row['acceptance']), resignation_date=day(row['resignation']), subscription_number=row['number'],
            subscription_type=row['kind'], custom_data=row['custom'], additional_fields=row['additional'], notes=row['notes'],
            document_pdf_id=source['documents'][row['document']]['id'] if row['document'] else None,
            medical_id=source['certificates'][row['medical']]['id'] if row['medical'] else None,
            group_id=ids[row['group']] if row['group'] else None, deleted=row['deleted'], creation_date=moment(row['created'])))


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


def bindings(case, before, fixture, ids):
    from django.conf import settings
    users = keyed(before['application.User'], 'user_id')
    associations = keyed(before['application.SportAssociation'], 'sport_association_id')
    values = {'$app_url': settings.APP_URL, '$whitelabel': settings.WHITELABEL_NAME, '$is_whitelabel': settings.IS_WHITELABEL,
              '$denomination:own': associations[ids['own_association']]['denomination'],
              '$denomination:foreign': associations[ids['foreign_association']]['denomination']}
    if case['actor'] != 'anonymous':
        values['$authorization'] = 'Bearer ' + fixture['tokens'][case['actor']]
    for actor in ('owner', 'athlete', 'foreign_owner'):
        values[f'$email:{actor}'] = users[ids[actor]]['email']
    return values


def resolve(value, values):
    if isinstance(value, str):
        if value in values:
            return values[value]
        for key in ('$denomination:own', '$denomination:foreign'):
            if key in value:
                value = value.replace(key, str(values[key]))
        return value
    if isinstance(value, list):
        return [resolve(item, values) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, values) for key, item in value.items()}
    return value


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    since, until = context['prepared']['started'], time.time()
    effect = case['effect']
    values = bindings(case, before, fixture, ids)
    celery = resolve(effect['celery'], values) if effect else []
    harness.celery_published(contract, fixture, celery, since=since, until=until)
    if effect is None or effect['kind'] == 'reminder':
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    row = source['subscriptions'][effect['subscription']]
    expected = []
    for stored in before[SUB]:
        stored = flat(stored)
        if str(stored['subscription_id']) == row['id']:
            stored = {**stored, **effect['columns']}
        expected.append(stored)
    contract.require([flat(r) for r in after[SUB]] == expected, 'state.subscriptions')
    added, indexes = harness.audit_delta(contract, before, after)
    if effect['audit'] is None:
        contract.require(not added and not indexes, 'audit.none')
        return
    contract.require(len(added) == 1 and len(indexes) == 1, 'audit.count')
    event = added[0]
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    contract.require(event['content_type_id'] == types['subscription'] and event['action'] == 1 and event['object_pk'] == row['id']
                     and event['object_repr'] == f"Subscription object ({row['id']})", 'audit.identity')
    contract.require(event['changes'] == effect['audit'], 'audit.changes')
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


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
