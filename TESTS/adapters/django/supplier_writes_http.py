"""Parent-only supplier add and update HTTP runner; imports are inert.

Seeds the supplier-list rows (audit disabled) and verifies each case against
the complete model state: an add creates one supplier with its auditlog
create entry and, unless the body named the association, an association
update entry (a deleted supplier repeats the whole diff, as the
soft-delete manager cannot load it); an update changes only the named fields with one update
entry when something changed. New suppliers are removed before the fixture
cleanup compares the model state.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.contracts import test_supplier_writes_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django import suppliers_http as suppliers
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/supplier_writes_http.json', 'TESTS/contracts/test_supplier_writes_http.py',
           'TESTS/adapters/django/supplier_writes_http.py', 'TESTS/adapters/django/suppliers_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'BE/application/views/supplier_views.py',
           'BE/application/serializers/payment_serializers.py', 'BE/application/models/payment_models.py', 'BE/application/tenant_scope.py',
           'BE/application/mixin.py', 'BE/application/audit_resolvers.py')
SUPPLIER, AUDIT, INDEX = 'application.SupplierAndCustomers', 'auditlog.LogEntry', 'application.AuditLogIndex'
MANAGED = (SUPPLIER, AUDIT, INDEX)
FIELDS = ('name', 'address', 'tax_code', 'vat_number', 'type', 'email', 'phone_number', 'city', 'cap', 'state', 'country', 'nationality', 'note')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        suppliers.seed_rows(contract.load_source(), fixture['created'])
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models.payment_models import SupplierAndCustomers
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    original = list(SupplierAndCustomers._base_manager.values_list('pk', flat=True))
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
                    seeded = [pk for model, pk in fixture['created']]
                    SupplierAndCustomers._base_manager.exclude(pk__in=[*original, *seeded]).delete()
                    shared.delete_created(fixture)
            carnets.state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def stored(row):
    return {k: row[k] for k in ('supplier_id', *FIELDS, 'sport_association_id', 'group_id', 'deleted')}


def assert_audit(event, case, actor, users, remote, model_type, key, action, changes, index, indexes):
    contract.require(event['content_type_id'] == model_type and event['action'] == action and event['object_pk'] == key
                     and event['object_repr'] == f'SupplierAndCustomers object ({key})', f'audit.identity:{action}')
    contract.require(event['changes'] == changes, f'audit.changes:{action}')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    row = indexes.pop(str(event['id']), None)
    contract.require((row is None) if index is None else (row is not None and row['sport_association_id'] == index[0]
                                                           and row['resolution_path'] == index[1]), f'index.fields:{action}')


def assert_effect(case, before, after, source, remote, fixture):
    from application.models.payment_models import SupplierAndCustomers
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    old_s, new_s = keyed(before[SUPPLIER], 'supplier_id'), keyed(after[SUPPLIER], 'supplier_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = sorted((new_a[k] for k in set(new_a) - set(old_a)), key=lambda r: int(r['id']))
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(all(new_a[k] == old_a[k] for k in old_a) and all(new_i[k] == old_i[k] for k in old_i), 'audit.kept')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    if effect['kind'] == 'update':
        key = source['suppliers'][effect['supplier']]['id']
        expected = dict(old_s[key])
        for field, (_, value) in effect['changes'].items():
            expected[field] = None if value == 'None' else value
        response = case['response']
        for field in FIELDS:
            expected[field] = response[field]
        contract.require(set(new_s) == set(old_s) and all(new_s[k] == old_s[k] for k in old_s if k != key)
                         and stored(new_s[key]) == stored(expected), 'state.supplier')
        contract.require(len(added) == (1 if effect['changes'] else 0), 'audit.count')
        for event in added:
            assert_audit(event, case, actor, users, remote, types['supplierandcustomers'], key, 1, effect['changes'], (own, 'direct'), indexes)
        contract.require(not indexes, 'index.extra')
        return
    new_keys = set(new_s) - set(old_s)
    contract.require(len(new_keys) == 1 and len(new_s) == len(old_s) + 1 and all(new_s[k] == old_s[k] for k in old_s), 'state.created')
    key = new_keys.pop()
    values = effect['values']
    want = {'supplier_id': key, **{f: values[f] for f in FIELDS}, 'sport_association_id': own, 'group_id': values['group'],
            'deleted': values['deleted']}
    contract.require(stored(new_s[key]) == want and case['response']['supplier_id'] in ('$new', key), 'state.supplier')
    row = SupplierAndCustomers._base_manager.get(pk=key)
    created = topup.created_changes(SupplierAndCustomers, row, {})
    body = case.get('body') if isinstance(case.get('body'), dict) else {}
    named = 'sport_association' in body
    if not named:
        created['sport_association'] = ['None', 'None']
    via_changes = (own, 'changes_json.sport_association')
    entries = [(0, created, via_changes if named else (own, 'actor.sport_association') if effect['actor'] == 'owner' else None)]
    if values['deleted']:
        # The soft-delete manager cannot load a deleted row, so auditlog's
        # pre_save diffs the association save against nothing again.
        entries.append((1, dict(created, sport_association=['None', own]), via_changes))
    elif not named:
        entries.append((1, {'sport_association': ['None', own]}, via_changes))
    contract.require(len(added) == len(entries), 'audit.count')
    for event, (action, changes, index) in zip(added, entries):
        assert_audit(event, case, actor, users, remote, types['supplierandcustomers'], key, action, changes, index, indexes)
    contract.require(not indexes, 'index.extra')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
