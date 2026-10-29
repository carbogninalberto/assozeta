"""Parent-only payment/<uid>/archive HTTP runner; imports are inert.

Seeds a payment category and payments (audit disabled) and verifies each case
against the complete model state: only the target payment's archived flag
(and a filled default category) may change, with one exact auditlog update
entry indexed directly to the payment's association.
"""
from contextlib import contextmanager
from decimal import Decimal
import ipaddress
from pathlib import Path

from TESTS.contracts import test_payment_archive_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/payment_archive_http.json', 'TESTS/contracts/test_payment_archive_http.py',
           'TESTS/adapters/django/payment_archive_http.py', 'BE/application/views/payment_views.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py', 'BE/application/audit_resolvers.py')
PAYMENT, AUDIT, INDEX = 'application.Payment', 'auditlog.LogEntry', 'application.AuditLogIndex'


def default_category():
    from application.models import PaymentCategory
    row = PaymentCategory.objects.filter(name__iexact='entrate e proventi da attività tipiche').order_by('pk').first()
    return str(row.pk) if row else None


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import Payment, PaymentCategory
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    ids = source['ids']
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        PaymentCategory._base_manager.bulk_create([PaymentCategory(payment_category_id=source['category'], name='Categoria fixture',
                                                                   sport_association_id=ids['own_association'])])
        fixture['created'].append((PaymentCategory, source['category']))
        from application.models import Carnet
        from application.models.carnet_models import CarnetSubscription
        Carnet._base_manager.bulk_create([Carnet(carnet_id=source['carnet']['id'], title=source['carnet']['title'],
                                                 sport_association_id=ids['own_association'], fee=Decimal('1.00'))])
        fixture['created'].append((Carnet, source['carnet']['id']))
        for row in source['payments'].values():
            Payment._base_manager.bulk_create([Payment(payment_id=row['id'], sport_association_id=row['association'], amount=Decimal('10.00'),
                subject=row['subject'], description=row['description'], meta=row['meta'], course=row['course'], archived=row['archived'],
                deleted=row['deleted'], payment_category_id=source['category'] if row['category'] else None,
                group_id=ids[row['group']] if row['group'] else None)])
            fixture['created'].append((Payment, row['id']))
            if row['carnet']:
                holding = CarnetSubscription(carnet_id_id=source['carnet']['id'], payment_id=row['id'])
                CarnetSubscription._base_manager.bulk_create([holding])
                fixture['created'].append((CarnetSubscription, holding.pk))
        fixture['default_category'] = default_category()
        fixture['default_category_name'] = PaymentCategory.objects.get(pk=fixture['default_category']).name if fixture['default_category'] else None
        contract.require(fixture['default_category'] is not None, 'fixture.default-category')
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
            contract.require(before == scopes.snapshot_state(apps), 'state.changed')
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, source, remote, fixture):
    managed = (PAYMENT, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    old_p, new_p = keyed(before[PAYMENT], 'payment_id'), keyed(after[PAYMENT], 'payment_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    effect = case['effect']
    if effect is None:
        contract.require(old_p == new_p and old_a == new_a and old_i == new_i, 'state.no-effect')
        return
    target = source['payments'][effect['payment']]['id']
    bound = {'$default_category': fixture['default_category'], '$default_category_name': fixture['default_category_name']}
    changes = {k: [bound.get(v, v) for v in pair] for k, pair in effect['changes'].items()}
    expected_row = dict(old_p[target], archived=effect['archived'])
    if 'payment_category' in changes:
        expected_row['payment_category_id'] = fixture['default_category']
    if 'description' in changes:
        expected_row['description'] = changes['description'][1]
    contract.require(set(new_p) == set(old_p) and all(new_p[k] == old_p[k] for k in old_p if k != target) and new_p[target] == expected_row,
                     'state.payment')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == 1 and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event, ids = added[0], source['ids']
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'payment']
    contract.require(len(types) == 1 and event['content_type_id'] == types[0] and event['object_pk'] == target and event['action'] == 1
                     and event['object_repr'] == f'Payment object ({target})', 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    if effect['impersonation']:
        additional = {'impersonation_session': fixture['sessions'][effect['impersonation']], 'actor_id': actor,
                      # Administrator sessions carry no association.
                      'target_user_id': case['headers']['User-Id'],
                      'association_id': None if effect['impersonation'].startswith('admin_') else ids['own_association']}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = [new_i[k] for k in set(new_i) - set(old_i)]
    contract.require(len(added_i) == 1 and added_i[0]['log_entry_id'] == event['id']
                     and added_i[0]['sport_association_id'] == source['payments'][effect['payment']]['association']
                     and added_i[0]['resolution_path'] == 'direct', 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    contract.require(fixture['baseline_state'] == scopes.snapshot_state(apps), 'state.changed')
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
