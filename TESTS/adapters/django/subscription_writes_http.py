"""Parent-only subscription/<uid>/edit HTTP runner; imports are inert.

Seeds a family, associates and tutor relations (audit disabled) and verifies
each case against the complete model state: exactly the authored associate
rows change, at most one associate is created (its generated key and creation
time are bound from the row), the authored associates and tutor relations are
deleted, the other relations keep their state, and the authored auditlog
entries are appended in order (delete entries rendered from the rows captured
before the request, associates with their reverse accessors) with their index
rows.
"""
from datetime import date, datetime, timedelta, timezone
import ipaddress

from TESTS.contracts import test_subscription_writes_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_writes_http.json', 'TESTS/contracts/test_subscription_writes_http.py',
           'TESTS/adapters/django/subscription_writes_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/user_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/invoices_models.py', 'BE/application/mixin.py',
           'BE/application/tenant_scope.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py',
           'BE/core/settings.py') + harness.SOURCES
ASSOCIATE, RELATION, FAMILY = 'application.Associate', 'application.AssociateTutorRelation', 'application.Family'
MANAGED = (ASSOCIATE, RELATION, harness.AUDIT, harness.INDEX)
SEEDED_AT = '2025-03-01T08:00:00Z'


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def seed_rows(source, created):
    from application.models import Associate, AssociateTutorRelation, Family
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    insert(Family, Family(family_id=source['family'], type='family'))
    for label, row in source['associates'].items():
        insert(Associate, Associate(
            associate_id=row['id'], sport_association_id=row['association'], user_id=ids[row['user']] if row['user'] else None,
            first_name=row['first'], last_name=row['last'], sex='M', is_minor=row['is_minor'], tax_code=row['tax_code'],
            born_date=date.fromisoformat(row['born']) if row['born'] else None, group_id=ids[row['group']] if row['group'] else None,
            deleted=row['deleted'], creation_date=moment(source['created'].get(label, SEEDED_AT))))
    for row in source['relations'].values():
        insert(AssociateTutorRelation, AssociateTutorRelation(
            id=row['id'], associate_id=source['associates'][row['associate']]['id'], tutor_id=source['associates'][row['tutor']]['id'],
            is_primary=row['primary']))


def associate_model():
    from application.models import Associate
    return Associate


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(associate_model,))


def captured(source):
    """auditlog's delete diffs of the seeded rows, captured before each case."""
    from application.models import Associate, AssociateTutorRelation
    rows = {}
    for row in Associate._base_manager.filter(pk__in=[r['id'] for r in source['associates'].values()]):
        rows[str(row.pk)] = {**replace.deleted_changes(Associate, row), **{k: [v, 'None'] for k, v in source['reverse'].items()}}
    for row in AssociateTutorRelation._base_manager.filter(pk__in=[r['id'] for r in source['relations'].values()]):
        rows[str(row.pk)] = replace.deleted_changes(AssociateTutorRelation, row)
    return rows


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    if isinstance(value, date):
        return value.isoformat()
    return value


def flat(row):
    return {key: plain(value) for key, value in row.items()}


def bind(value, bindings):
    if isinstance(value, dict):
        return {key: bind(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, bindings) for item in value]
    return bindings.get(value, value) if isinstance(value, str) else value


def audit_moment(text):
    """str() of the naive UTC value auditlog renders for a DateTimeField."""
    value = moment(text).astimezone(timezone.utc).replace(tzinfo=None)
    return str(value)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], source['ids']
    labels = {label: row['id'] for label, row in source['associates'].items()}
    relation_ids = {label: row['id'] for label, row in source['relations'].items()}

    old = {str(flat(r)['associate_id']): flat(r) for r in before[ASSOCIATE]}
    new = {str(flat(r)['associate_id']): flat(r) for r in after[ASSOCIATE]}
    added = set(new) - set(old)
    bindings = {}
    if effect['created'] is None:
        contract.require(not added, 'state.associate-created')
    else:
        contract.require(len(added) == 1, 'state.associate-created')
        key = added.pop()
        row = new.pop(key)
        stamp = moment(row['creation_date'])
        contract.require(abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=10), 'state.associate-creation-date')
        bindings = {'$new': key, '$now': audit_moment(row['creation_date'])}
        expected = bind({**effect['created'], 'associate_id': '$new', 'creation_date': row['creation_date']}, bindings)
        expected['associate_id'] = key
        contract.require(row == expected, 'state.associate-created-row')
    gone = {labels[label] for label in effect['deleted']}
    changes = {labels[label]: columns for label, columns in effect['changed'].items()}
    expected = {}
    for key, row in old.items():
        if key in gone:
            continue
        expected[key] = {**row, **changes.get(key, {})}
    contract.require(new == expected, 'state.associates')

    gone_relations = {relation_ids[label] for label in effect['relations_deleted']}
    relation_changes = {relation_ids[label]: columns for label, columns in effect['relations_changed'].items()}
    kept = [{**flat(r), **relation_changes.get(flat(r)['id'], {})} for r in before[RELATION] if flat(r)['id'] not in gone_relations]
    contract.require([flat(r) for r in after[RELATION]] == kept, 'state.relations')

    entries, indexes = harness.audit_delta(contract, before, after)
    entries.sort(key=lambda row: int(row['id']))
    contract.require(len(entries) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    for position, (event, entry) in enumerate(zip(entries, effect['audit'])):
        if entry['model'] == 'associate':
            key, name = bindings['$new'] if entry['label'] == '$new' else labels[entry['label']], 'Associate'
        else:
            key, name = str(relation_ids[entry['label']]), 'AssociateTutorRelation'
        where = f":{position}:{entry['model']}:{entry['label']}"
        contract.require(event['content_type_id'] == types[entry['model']] and event['action'] == entry['action'] and event['object_pk'] == key
                         and event['object_repr'] == f'{name} object ({key})', 'audit.identity' + where)
        changes = entry['changes']
        if changes is None:
            changes = {**context['prepared'][key], **entry.get('overrides', {})}
        contract.require(event['changes'] == bind(changes, bindings), 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if entry['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == entry['index'][0]
                             and index['resolution_path'] == entry['index'][1], 'index.fields' + where)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
