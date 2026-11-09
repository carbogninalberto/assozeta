"""Parent-only DefaultRouter personas HTTP runner; imports are inert.

Reuses the personas-writes seed and effect check for creates and partial
updates. The stock destroy is Associate.delete() through Django's Collector:
the case's associate row and the tutor relations naming it disappear (and
nothing else changes), with one auditlog delete entry per row, relations
before the associate, each with auditlog's delete diff of the row read
before the request (an associate's reverse relation managers rendered as
their empty manager) and its index row.
"""
import ipaddress

from TESTS.contracts import test_router_personas_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import personas_writes_http as personas
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/router_personas_http.json', 'TESTS/fixtures/generate_personas_writes_http.py',
           'TESTS/contracts/test_router_personas_http.py', 'TESTS/adapters/django/router_personas_http.py') \
    + tuple(source for source in personas.SOURCES if source not in harness.SOURCES and 'personas_writes_http.json' not in source
            and 'test_personas_writes_http' not in source) + ('TESTS/contracts/test_personas_writes_http.py',) \
    + harness.SOURCES
fixture_context = harness.fixture_context_for(contract, personas.seed_rows, purge=(personas.associate_model,))


def captured(source):
    """Relations (personas-writes) and associates: auditlog's delete diffs before each case."""
    from application.models import Associate
    rows = personas.captured(source)
    for row in Associate._base_manager.filter(pk__in=[r['id'] for r in source['associates'].values()]):
        rows[str(row.pk)] = {**replace.deleted_changes(Associate, row), **{name: [manager, 'None'] for name, manager in source['reverse'].items()}}
    return rows


def assert_destroy(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, personas.MANAGED)
    effect, ids = case['effect'], source['ids']
    key = source['associates'][effect['associate']]['id']
    relations = [str(source['relations'][label]['id']) for label in effect['relations']]
    flat = lambda rows, column: {str(personas.flat(r)[column]): personas.flat(r) for r in rows}
    old_a, new_a = flat(before[personas.ASSOCIATE], 'associate_id'), flat(after[personas.ASSOCIATE], 'associate_id')
    contract.require(key in old_a and {k: v for k, v in old_a.items() if k != key} == new_a, 'state.associates')
    old_r, new_r = flat(before[personas.RELATION], 'id'), flat(after[personas.RELATION], 'id')
    contract.require({k: v for k, v in old_r.items() if k not in relations} == new_r and set(relations) <= set(old_r), 'state.relations')
    entries, indexes = harness.audit_delta(contract, before, after)
    entries.sort(key=lambda row: int(row['id']))
    expected = [('associatetutorrelation', 'AssociateTutorRelation', rel, effect['relation_index']) for rel in relations]
    expected.append(('associate', 'Associate', key, effect['associate_index']))
    contract.require(len(entries) == len(expected) and len(indexes) == sum(1 for e in expected if e[3]), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    for position, (event, (model, name, pk, index)) in enumerate(zip(entries, expected)):
        where = f':{position}:{model}:{pk}'
        contract.require(event['content_type_id'] == types[model] and event['action'] == 2 and event['object_pk'] == pk
                         and event['object_repr'] == f'{name} object ({pk})', 'audit.identity' + where)
        contract.require(event['changes'] == context['prepared'][pk], 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        row = indexes.get(str(event['id']))
        contract.require((row is None) if index is None else (row is not None and [row['sport_association_id'], row['resolution_path']] == index),
                         'index.fields' + where)


def assert_effect(case, before, after, source, context):
    if case['effect'] and case['effect'].get('kind') == 'destroy':
        return assert_destroy(case, before, after, source, context)
    return personas.assert_effect(case, before, after, source, context)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
