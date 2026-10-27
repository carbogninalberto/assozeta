"""Parent-only check-inconsistencies HTTP runner; imports are inert.

Seeds the shared identity fixture plus the athlete's associates and tutor
relations (audit disabled). Every case must leave the complete model state
unchanged, except the authored promotion: one relation becomes primary with
one exact auditlog update entry indexed through its associate.
"""
from contextlib import contextmanager
from datetime import date
import ipaddress
from pathlib import Path

from TESTS.contracts import test_inconsistencies_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/inconsistencies_http.json', 'TESTS/contracts/test_inconsistencies_http.py',
           'TESTS/adapters/django/inconsistencies_http.py', 'BE/application/views/statistic_views.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/mixin.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py')
RELATION, AUDIT, INDEX = 'application.AssociateTutorRelation', 'auditlog.LogEntry', 'application.AuditLogIndex'


def state_unchanged(before, after):
    # Associate.born_date snapshots are date objects, which the shared JSON
    # digest cannot encode; compare the complete snapshots directly.
    contract.require(before == after, 'state.changed')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import Associate, AssociateTutorRelation
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    ids = source['ids']
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        rows = source['associates']
        for label, row in rows.items():
            values = {k: row[k] for k in ('first_name', 'last_name', 'email', 'sex', 'tax_code', 'address', 'address_cap',
                                          'address_city', 'born_city', 'is_minor', 'phone', 'family_role', 'deleted')}
            Associate._base_manager.bulk_create([Associate(
                associate_id=row['id'], user_id=ids['athlete'], sport_association_id=row['association'],
                born_date=date.fromisoformat(row['born_date']) if row['born_date'] else None,
                group_id=ids[row['group']] if row['group'] else None, **values)])
            fixture['created'].append((Associate, row['id']))
        fixture['relations'] = {}
        for child, tutor, primary in source['relations']:
            relation = AssociateTutorRelation._base_manager.create(associate_id=rows[child]['id'], tutor_id=rows[tutor]['id'],
                                                                  is_primary=primary)
            fixture['created'].insert(0, (AssociateTutorRelation, relation.pk))
            fixture['relations'][child] = relation.pk
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
                    shared.delete_created(fixture)
            state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, fixture, source, remote):
    managed = (RELATION, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    old_r, new_r = keyed(before[RELATION], 'id'), keyed(after[RELATION], 'id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    if not case['promotes']:
        contract.require(old_r == new_r and old_a == new_a and old_i == new_i, 'state.no-effect')
        return
    promoted = str(fixture['relations']['minor_secondary_tutor'])
    contract.require(set(old_r) == set(new_r) and old_r[promoted]['is_primary'] is False and new_r[promoted]['is_primary'] is True
                     and all(new_r[k] == old_r[k] for k in old_r if k != promoted), 'state.promoted-only')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == 1 and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event = added[0]
    ids = source['ids']
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'associatetutorrelation']
    contract.require(len(types) == 1 and event['content_type_id'] == types[0] and event['object_pk'] == promoted and event['action'] == 1
                     and event['object_repr'] == f'AssociateTutorRelation object ({promoted})', 'audit.identity')
    contract.require(event['changes'] == {'is_primary': ['False', 'True']}, 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    contract.require(event['actor_id'] == ids['athlete'] and event['actor_email'] == users[ids['athlete']]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = [new_i[k] for k in set(new_i) - set(old_i)]
    contract.require(len(added_i) == 1 and added_i[0]['log_entry_id'] == event['id'] and added_i[0]['sport_association_id'] == ids['own_association']
                     and added_i[0]['resolution_path'] == 'associate.sport_association', 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, fixture, source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
