"""Parent-only carnet add HTTP runner; imports are inert.

Seeds the carnet-assign rows (audit disabled) and verifies each case against
the complete model state: one new carnet with its create and association
update entries, then one payment and carnet subscription (two create entries)
per assigned subscription. New carnets are removed before the fixture
cleanup compares the model state.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import ipaddress
import json
from pathlib import Path

from TESTS.contracts import test_carnet_add_http as contract
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/carnet_add_http.json', 'TESTS/contracts/test_carnet_add_http.py', 'TESTS/adapters/django/carnet_add_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'TESTS/adapters/django/carnet_list_http.py',
           'BE/application/views/carnet_views.py', 'BE/application/serializers/carnet_serializers.py', 'BE/application/models/carnet_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/balance_sheet_models.py', 'BE/application/audit_resolvers.py')
CARNET = 'application.Carnet'
PAYMENT, HOLDING, LINK, AUDIT, INDEX = topup.PAYMENT, topup.HOLDING, topup.LINK, topup.AUDIT, topup.INDEX
MANAGED = (*topup.MANAGED, CARNET)
plain = topup.plain


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from application.models.carnet_models import Carnet
    from application.models.carnet_models import CarnetSubscription
    original = list(Carnet._base_manager.values_list('pk', flat=True))
    seeded = {str(row['id']) for row in contract.load_source()['carnets'].values()}
    with topup.fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt, source_contract=contract) as fixture:
        try:
            yield fixture
        finally:
            # New carnets go first; their payments go with the topup cleanup.
            with disable_auditlog():
                added = Carnet._base_manager.exclude(pk__in=original).exclude(pk__in=seeded)
                CarnetSubscription._base_manager.filter(carnet_id__in=added).delete()
                added.delete()


def assert_effect(case, before, after, source, remote, fixture, window):
    from application.models import Payment
    from application.models.carnet_models import Carnet, CarnetSubscription
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    for model in MANAGED[:3] + (CARNET,):
        contract.require(all(row in after[model] for row in before[model]), 'state.kept:' + model)
    new_carnets = [plain(r['carnet_id']) for r in after[CARNET] if r not in before[CARNET]]
    contract.require(len(new_carnets) == 1 and len(after[CARNET]) == len(before[CARNET]) + 1, 'state.carnet-created')
    carnet = Carnet._base_manager.get(pk=new_carnets[0])
    spec = effect['carnet']
    contract.require(carnet.title == spec['title'] and carnet.description == spec['description'] and carnet.lessons_number == spec['lessons']
                     and carnet.fee == Decimal(spec['fee']) and carnet.meta == spec['meta'] and carnet.public is True
                     and carnet.user_id_id is None and str(carnet.sport_association_id) == ids['own_association']
                     and window[0] <= carnet.creation_date <= window[1], 'state.carnet')
    holdings = list(CarnetSubscription._base_manager.filter(carnet_id=carnet))
    expected = {source['subscriptions'][h['subscription']]['id']: h for h in effect['holdings']}
    contract.require(len(holdings) == len(expected) and {str(h.subscription_id) for h in holdings} == set(expected), 'state.holdings')
    new_payments = {plain(r['payment_id']) for r in after[PAYMENT] if r not in before[PAYMENT]}
    contract.require(new_payments == {str(h.payment_id) for h in holdings} and len(after[HOLDING]) == len(before[HOLDING]) + len(holdings)
                     and len(after[PAYMENT]) == len(before[PAYMENT]) + len(holdings) and after[LINK] == before[LINK], 'state.payments')
    meta = {'lessons_counter': spec['lessons'], 'lessons_left': spec['lessons'], 'lessons_registry': []}
    expectations = {}
    for holding in holdings:
        subscription = next(s for s in source['subscriptions'].values() if s['id'] == str(holding.subscription_id))
        payment = Payment._base_manager.get(pk=holding.payment_id)
        account = source['accounts'][expected[str(holding.subscription_id)]['account']]['id']
        stored = {f.attname: getattr(payment, f.attname) for f in Payment._meta.concrete_fields}
        want = {**stored, 'type': 'default', 'subject': 2, 'amount': Decimal(spec['fee']), 'payment_date': None, 'paid': False, 'expense': False,
                'description': 'Quota Corso', 'payment_category_id': fixture['default_category'], 'meta_payment_categories': None, 'invoice_id': None,
                'user_id': ids[subscription['user']], 'associate_id': source['associates'][subscription['associate']]['id'], 'supplier_id': None,
                'instructor_id': None, 'sport_association_id': ids['own_association'], 'payment_intent_id': None, 'archived': False,
                'invoice_fiscal_code': None, 'meta': None, 'notes': None, 'custom_accounts_id': account, 'signature_id': None, 'deleted': False,
                'group_id': None, 'course': None}
        text = lambda row: {k: str(v) if k.endswith('_id') and v is not None else v for k, v in row.items()}
        contract.require(text(stored) == text(want) and window[0] <= payment.creation_date <= window[1], 'state.payment')
        contract.require(str(holding.user_id_id) == ids[subscription['user']] and holding.disabled is False and holding.meta == meta, 'state.holding')
        expectations[str(payment.pk)] = ('payment', 'Payment', topup.created_changes(Payment, payment, {
            'amount': str(payment.amount), 'creation_date': topup.auditlog_moment(payment.creation_date)}), (ids['own_association'], 'direct'))
        expectations[str(holding.pk)] = ('carnetsubscription', 'CarnetSubscription', topup.created_changes(CarnetSubscription, holding, {
            'creation_date': topup.auditlog_moment(holding.creation_date), 'meta': json.dumps(meta, sort_keys=True)}),
            (subscription['association'], 'subscription.sport_association'))
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = sorted((new_a[k] for k in set(new_a) - set(old_a)), key=lambda r: int(r['id']))
    contract.require(len(added) == 2 + 2 * len(holdings) and all(new_a[k] == old_a[k] for k in old_a), 'audit.entries')
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(all(new_i[k] == old_i[k] for k in old_i), 'index.kept')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    created = dict(topup.created_changes(Carnet, carnet, {'creation_date': topup.auditlog_moment(carnet.creation_date),
        'fee': spec['fee'], 'meta': json.dumps(spec['meta'], sort_keys=True) if spec['meta'] is not None else None}))
    created['sport_association'] = ['None', 'None']
    actor_index = (ids['own_association'], 'actor.sport_association') if effect['actor'] == 'owner' else None
    carnet_entries = [(0, created, actor_index), (1, {'sport_association': ['None', ids['own_association']]},
                                                          (ids['own_association'], 'changes_json.sport_association'))]
    for event, (action, changes, index) in zip(added[:2], carnet_entries):
        contract.require(event['content_type_id'] == types['carnet'] and event['action'] == action and event['object_pk'] == str(carnet.pk)
                         and event['object_repr'] == f'Carnet object ({carnet.pk})', 'audit.carnet-identity')
        contract.require(event['changes'] == changes, f'audit.carnet-changes:{action}')
        row = indexes.pop(str(event['id']), None)
        contract.require((row is None) if index is None else (row is not None and row['sport_association_id'] == index[0]
                                                               and row['resolution_path'] == index[1]), f'index.carnet:{action}')
    for event in added[2:]:
        model, name, changes, index = expectations[event['object_pk']]
        contract.require(event['content_type_id'] == types[model] and event['action'] == 0
                         and event['object_repr'] == f'{name} object ({event["object_pk"]})', 'audit.identity:' + model)
        contract.require(event['changes'] == changes, 'audit.changes:' + model)
        row = indexes.pop(str(event['id']), None)
        contract.require(row is not None and row['sport_association_id'] == index[0] and row['resolution_path'] == index[1], 'index.fields:' + model)
    contract.require(not indexes, 'index.extra')
    for event in added:
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')


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
            start = datetime.now(timezone.utc)
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            window = (start, datetime.now(timezone.utc))
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture, window)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
