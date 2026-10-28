"""Parent-only carnet-subscription topup HTTP runner; imports are inert.

Seeds the carnet-list rows and bank accounts (audit disabled) and verifies
each case against the complete model state: a successful topup only adds
one payment, one carnet subscription with its course links, and exactly
two auditlog create entries with their indexes. Created rows are removed
before the fixture cleanup compares the model state.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import ipaddress
import json
from pathlib import Path

from TESTS.contracts import test_carnet_subscription_topup_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.payment_archive_http import default_category
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/carnet_subscription_topup_http.json', 'TESTS/contracts/test_carnet_subscription_topup_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'TESTS/adapters/django/carnet_list_http.py',
           'BE/application/views/carnet_views.py', 'BE/application/models/carnet_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py')
PAYMENT, HOLDING, LINK = 'application.Payment', 'application.CarnetSubscription', 'application.CarnetSubscription_course_subscription'
AUDIT, INDEX = 'auditlog.LogEntry', 'application.AuditLogIndex'
MANAGED = (PAYMENT, HOLDING, LINK, AUDIT, INDEX)


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import CustomAccounts
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        carnets.seed_rows(source, fixture['created'])
        for row in source['accounts'].values():
            CustomAccounts._base_manager.bulk_create([CustomAccounts(custom_account_id=row['id'], sport_association_id=row['association'],
                name=row['name'], initial_balance=Decimal('0.00'), account_type=row['type'], account_code='', editable=row['editable'],
                enabled=row['enabled'])])
            fixture['created'].append((CustomAccounts, row['id']))
        fixture['default_category'] = default_category()
        contract.require(fixture['default_category'] is not None, 'fixture.default-category')
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import Payment
    from application.models.carnet_models import CarnetSubscription
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    original_payments = list(Payment._base_manager.values_list('pk', flat=True))
    original_holdings = list(CarnetSubscription._base_manager.values_list('pk', flat=True))
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
                    seeded = {pk for model, pk in fixture['created']}
                    CarnetSubscription._base_manager.exclude(pk__in=[*original_holdings, *seeded]).delete()
                    Payment._base_manager.exclude(pk__in=[*original_payments, *seeded]).delete()
                    shared.delete_created(fixture)
            contract.require(before == scopes.snapshot_state(apps), 'state.changed')
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def plain(value):
    return value.get('value') if isinstance(value, dict) else value


def auditlog_moment(value):
    """str() of the naive UTC datetime auditlog stores for a DateTimeField."""
    return str(value.astimezone(timezone.utc).replace(tzinfo=None))


def created_changes(model, row, values):
    """auditlog's create diff: every field against None, None-valued fields kept
    only for foreign keys and DateTimeFields ('None') and JSON fields ('null')."""
    changes = {}
    for field in model._meta.concrete_fields:
        value = getattr(row, field.attname)
        if value is None:
            if field.is_relation or field.get_internal_type() == 'DateTimeField':
                changes[field.name] = ['None', 'None']
            elif field.get_internal_type() == 'JSONField':
                changes[field.name] = ['None', 'null']
            continue
        changes[field.name] = ['None', values.get(field.name, str(value))]
    return changes


def assert_effect(case, before, after, source, remote, fixture, window):
    from application.models import Payment
    from application.models.carnet_models import CarnetSubscription
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    for model in (PAYMENT, HOLDING, LINK):
        contract.require(all(row in after[model] for row in before[model]), 'state.kept:' + model)
    new_payments = [plain(r['payment_id']) for r in after[PAYMENT] if r not in before[PAYMENT]]
    new_holdings = [plain(r['carnet_subscription_id']) for r in after[HOLDING] if r not in before[HOLDING]]
    contract.require(len(new_payments) == 1 and len(new_holdings) == 1 and len(after[PAYMENT]) == len(before[PAYMENT]) + 1
                     and len(after[HOLDING]) == len(before[HOLDING]) + 1, 'state.created')
    payment = Payment._base_manager.get(pk=new_payments[0])
    holding = CarnetSubscription._base_manager.get(pk=new_holdings[0])
    old = source['carnet_subscriptions'][effect['holding']]
    carnet = source['carnets'][old['carnet']]
    subscription = source['subscriptions'][old['subscription']]
    spec = effect['payment']
    account = source['accounts'][spec['account']]['id'] if spec['account'] else None
    contract.require(window[0] <= payment.creation_date <= window[1] and window[0] <= holding.creation_date <= window[1], 'state.created-time')
    stored = {f.attname: getattr(payment, f.attname) for f in Payment._meta.concrete_fields}
    expected = {**stored, 'type': 'default', 'subject': 2, 'amount': Decimal(spec['amount']), 'payment_date': None, 'paid': False,
                'expense': False, 'description': 'Quota Corso', 'payment_category_id': fixture['default_category'],
                'meta_payment_categories': None, 'invoice_id': None, 'user_id': ids[spec['user']],
                'associate_id': source['associates'][spec['associate']]['id'], 'supplier_id': None, 'instructor_id': None,
                'sport_association_id': spec['association'], 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None,
                'meta': None, 'notes': None, 'custom_accounts_id': account, 'signature_id': None, 'deleted': False, 'group_id': None,
                'course': None}
    contract.require({k: str(v) if k.endswith('_id') and v is not None else v for k, v in stored.items()}
                     == {k: str(v) if k.endswith('_id') and v is not None else v for k, v in expected.items()}, 'state.payment')
    lessons = effect['lessons']
    meta = {'lessons_counter': lessons, 'lessons_left': lessons, 'lessons_registry': []}
    contract.require(str(holding.carnet_id_id) == carnet['id'] and str(holding.subscription_id) == subscription['id']
                     and (str(holding.user_id_id) if holding.user_id_id else None) == (ids[old['user']] if old['user'] else None)
                     and str(holding.payment_id) == str(payment.pk) and holding.disabled is False and holding.meta == meta, 'state.holding')
    links = sorted(plain(r['coursesubscription_id']) for r in after[LINK] if r not in before[LINK])
    contract.require(links == sorted(source['course_subscriptions'][c]['id'] for c in effect['courses'])
                     and all(plain(r['carnetsubscription_id']) == str(holding.pk) for r in after[LINK] if r not in before[LINK]), 'state.links')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == 2 and all(new_a[k] == old_a[k] for k in old_a), 'audit.entries')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    additional = None
    if effect['impersonation']:
        additional = {'impersonation_session': fixture['sessions'][effect['impersonation']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'],
                      'association_id': None if effect['impersonation'].startswith('admin_') else ids['own_association']}
    dumps = lambda value: json.dumps(value, sort_keys=True)
    expectations = {
        str(payment.pk): ('payment', 'Payment', created_changes(Payment, payment, {
            'amount': spec['amount'], 'creation_date': auditlog_moment(payment.creation_date)}), (spec['association'], 'direct')),
        str(holding.pk): ('carnetsubscription', 'CarnetSubscription', created_changes(CarnetSubscription, holding, {
            'creation_date': auditlog_moment(holding.creation_date), 'meta': dumps(meta)}),
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
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        row = added_i.get(str(event['id']))
        contract.require(row is not None and row['sport_association_id'] == index[0] and row['resolution_path'] == index[1],
                         'index.fields:' + model)


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
