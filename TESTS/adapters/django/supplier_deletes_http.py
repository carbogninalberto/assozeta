"""Parent-only supplier delete and bulk-delete HTTP runner; imports are inert.

Seeds the supplier-list rows and a payment of one supplier (audit disabled)
and verifies each case against the complete model state: only the deleted
suppliers disappear, their payments lose the reference, and each delete has
one auditlog entry (rendered from the row before the request) indexed
through changes_json.
"""
from contextlib import contextmanager
from decimal import Decimal
import ipaddress
from pathlib import Path

from TESTS.contracts import test_supplier_deletes_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django import suppliers_http as suppliers
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/supplier_deletes_http.json', 'TESTS/contracts/test_supplier_deletes_http.py',
           'TESTS/adapters/django/supplier_deletes_http.py', 'TESTS/adapters/django/suppliers_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'BE/application/views/supplier_views.py',
           'BE/application/models/payment_models.py', 'BE/application/utils/api_utils.py', 'BE/application/mixin.py',
           'BE/application/audit_resolvers.py')
SUPPLIER, PAYMENT, AUDIT, INDEX = 'application.SupplierAndCustomers', 'application.Payment', 'auditlog.LogEntry', 'application.AuditLogIndex'
MANAGED = (SUPPLIER, PAYMENT, AUDIT, INDEX)


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import Payment
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        suppliers.seed_rows(source, fixture['created'])
        for row in source['payments'].values():
            Payment._base_manager.bulk_create([Payment(payment_id=row['id'], sport_association_id=row['association'], amount=Decimal(row['amount']),
                supplier_id=source['suppliers'][row['supplier']]['id'], expense=True)])
            fixture['created'].append((Payment, row['id']))
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
            carnets.state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, source, remote, fixture, rows):
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    gone = {source['suppliers'][label]['id'] for label in effect['deleted']}
    old_s, new_s = keyed(before[SUPPLIER], 'supplier_id'), keyed(after[SUPPLIER], 'supplier_id')
    contract.require(set(new_s) == set(old_s) - gone and all(new_s[k] == old_s[k] for k in new_s), 'state.suppliers')
    nulled = {source['payments'][label]['id'] for label in effect['nulled_payments']}
    old_p, new_p = keyed(before[PAYMENT], 'payment_id'), keyed(after[PAYMENT], 'payment_id')
    contract.require(set(new_p) == set(old_p) and all(new_p[k] == (dict(old_p[k], supplier_id=None) if k in nulled else old_p[k]) for k in old_p),
                     'state.payments')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == len(gone) and {e['object_pk'] for e in added} == gone and all(new_a[k] == old_a[k] for k in old_a),
                     'audit.entries')
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(len(indexes) == len(gone) and all(new_i[k] == old_i[k] for k in old_i), 'index.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    for event in added:
        key = event['object_pk']
        contract.require(event['content_type_id'] == types['supplierandcustomers'] and event['action'] == 2
                         and event['object_repr'] == f'SupplierAndCustomers object ({key})', 'audit.identity')
        contract.require(event['changes'] == rows[key], 'audit.changes')
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes[str(event['id'])]
        contract.require(index['sport_association_id'] == ids['own_association'] and index['resolution_path'] == 'changes_json.sport_association',
                         'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from application.models.payment_models import SupplierAndCustomers
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        rows = {str(r.pk): replace.deleted_changes(SupplierAndCustomers, r) for r in SupplierAndCustomers._base_manager.filter(
            pk__in=[s['id'] for s in source['suppliers'].values()])}
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture, rows)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
