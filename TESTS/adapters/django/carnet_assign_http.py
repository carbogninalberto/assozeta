"""Parent-only carnet assign HTTP runner; imports are inert.

Seeds the carnet-list rows, bank accounts and extra course rows (audit
disabled) and verifies each case against the complete model state: a create
adds one payment, one carnet subscription with its course link and two
auditlog create entries; a link only adds a course link, without audit.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import ipaddress
import json
from pathlib import Path

from TESTS.contracts import test_carnet_assign_http as contract
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/carnet_assign_http.json', 'TESTS/contracts/test_carnet_assign_http.py',
           'TESTS/adapters/django/carnet_assign_http.py', 'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'TESTS/adapters/django/carnet_list_http.py', 'BE/application/views/carnet_views.py', 'BE/application/models/carnet_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py', 'BE/application/models/balance_sheet_models.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py')
PAYMENT, HOLDING, LINK, AUDIT, INDEX = topup.PAYMENT, topup.HOLDING, topup.LINK, topup.AUDIT, topup.INDEX
MANAGED = topup.MANAGED
plain = topup.plain


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with topup.fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt, source_contract=contract) as fixture:
        yield fixture


def assert_link(case, before, after, source):
    effect = case['effect']
    contract.require(all(before[k] == after[k] for k in (PAYMENT, HOLDING, AUDIT, INDEX)), 'state.link-only')
    added = [r for r in after[LINK] if r not in before[LINK]]
    contract.require(all(r in after[LINK] for r in before[LINK]), 'state.links-kept')
    if effect['course'] is None:
        contract.require(not added and len(after[LINK]) == len(before[LINK]), 'state.no-link')
        return
    contract.require(len(added) == 1 and plain(added[0]['carnetsubscription_id']) == source['carnet_subscriptions'][effect['holding']]['id']
                     and plain(added[0]['coursesubscription_id']) == source['course_subscriptions'][effect['course']]['id'], 'state.link')


def assert_create(case, before, after, source, remote, fixture, window):
    from application.models import Payment
    from application.models.carnet_models import CarnetSubscription
    effect, ids = case['effect'], source['ids']
    for model in (PAYMENT, HOLDING, LINK):
        contract.require(all(row in after[model] for row in before[model]), 'state.kept:' + model)
    new_payments = [plain(r['payment_id']) for r in after[PAYMENT] if r not in before[PAYMENT]]
    new_holdings = [plain(r['carnet_subscription_id']) for r in after[HOLDING] if r not in before[HOLDING]]
    contract.require(len(new_payments) == 1 and len(new_holdings) == 1 and len(after[PAYMENT]) == len(before[PAYMENT]) + 1
                     and len(after[HOLDING]) == len(before[HOLDING]) + 1, 'state.created')
    payment = Payment._base_manager.get(pk=new_payments[0])
    holding = CarnetSubscription._base_manager.get(pk=new_holdings[0])
    carnet = source['carnets'][effect['carnet']]
    subscription = source['subscriptions'][effect['subscription']]
    spec = effect['payment']
    account = source['accounts'][spec['account']]['id'] if spec['account'] else None
    contract.require(window[0] <= payment.creation_date <= window[1] and window[0] <= holding.creation_date <= window[1], 'state.created-time')
    stored = {f.attname: getattr(payment, f.attname) for f in Payment._meta.concrete_fields}
    expected = {**stored, 'type': 'default', 'subject': 2, 'amount': Decimal(spec['amount']), 'payment_date': None, 'paid': False,
                'expense': False, 'description': 'Quota Corso', 'payment_category_id': fixture['default_category'],
                'meta_payment_categories': None, 'invoice_id': None, 'user_id': ids[spec['user']],
                'associate_id': source['associates'][spec['associate']]['id'], 'supplier_id': None, 'instructor_id': None,
                'sport_association_id': spec['association'], 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None,
                'meta': None, 'notes': None, 'custom_accounts_id': account, 'signature_id': None, 'deleted': False,
                'group_id': ids[spec['group']] if spec['group'] else None,
                'course': None}
    text = lambda row: {k: str(v) if k.endswith('_id') and v is not None else v for k, v in row.items()}
    contract.require(text(stored) == text(expected), 'state.payment')
    lessons = effect['lessons']
    meta = {'lessons_counter': lessons, 'lessons_left': lessons, 'lessons_registry': []}
    contract.require(str(holding.carnet_id_id) == carnet['id'] and str(holding.subscription_id) == subscription['id']
                     and (str(holding.user_id_id) if holding.user_id_id else None) == (ids[subscription['user']] if subscription['user'] else None)
                     and str(holding.payment_id) == str(payment.pk) and holding.disabled is False and holding.meta == meta, 'state.holding')
    added_links = [r for r in after[LINK] if r not in before[LINK]]
    contract.require(sorted(plain(r['coursesubscription_id']) for r in added_links)
                     == sorted(source['course_subscriptions'][c]['id'] for c in effect['courses'])
                     and all(plain(r['carnetsubscription_id']) == str(holding.pk) for r in added_links), 'state.links')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == 2 and all(new_a[k] == old_a[k] for k in old_a), 'audit.entries')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    dumps = lambda value: json.dumps(value, sort_keys=True)
    expectations = {
        str(payment.pk): ('payment', 'Payment', topup.created_changes(Payment, payment, {
            'amount': spec['amount'], 'creation_date': topup.auditlog_moment(payment.creation_date)}), (spec['association'], 'direct')),
        str(holding.pk): ('carnetsubscription', 'CarnetSubscription', topup.created_changes(CarnetSubscription, holding, {
            'creation_date': topup.auditlog_moment(holding.creation_date), 'meta': dumps(meta)}),
            (subscription['association'], 'subscription.sport_association')),
    }
    added_i = {str(row['log_entry_id']): row for row in (new_i[k] for k in set(new_i) - set(old_i))}
    contract.require(len(added_i) == 2 and all(new_i[k] == old_i[k] for k in old_i), 'index.count')
    for event in added:
        model, name, changes, index = expectations[event['object_pk']]
        contract.require(event['content_type_id'] == types[model] and event['action'] == 0
                         and event['object_repr'] == f'{name} object ({event["object_pk"]})', 'audit.identity:' + model)
        contract.require(event['changes'] == changes, 'audit.changes:' + model)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        row = added_i.get(str(event['id']))
        contract.require(row is not None and row['sport_association_id'] == index[0] and row['resolution_path'] == index[1],
                         'index.fields:' + model)


def assert_effect(case, before, after, source, remote, fixture, window):
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
    elif effect['kind'] == 'link':
        assert_link(case, before, after, source)
    else:
        assert_create(case, before, after, source, remote, fixture, window)


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
