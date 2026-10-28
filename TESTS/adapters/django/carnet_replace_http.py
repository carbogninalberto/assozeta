"""Parent-only carnet replace HTTP runner; imports are inert.

Seeds the carnet-assign rows plus installments and payments of a course
subscription (audit disabled) and verifies each case against the complete
model state: the ordered auditlog delete entries of the signal cascade
(rendered from the rows before the request), the removed rows, the course
subscription whose payment was deleted, then the carnet_assign effect.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import ipaddress
import json
from pathlib import Path

from TESTS.contracts import test_carnet_replace_http as contract
from TESTS.adapters.django import carnet_assign_http as assign
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/carnet_replace_http.json', 'TESTS/contracts/test_carnet_replace_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'TESTS/adapters/django/carnet_assign_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'TESTS/adapters/django/carnet_list_http.py', 'BE/application/views/carnet_views.py', 'BE/application/models/carnet_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py', 'BE/application/signals.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py')


def seed_fixture(apps, private_work, raw, *, source_contract=contract):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import CourseSubscription, CourseSubscriptionInstallment
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = source_contract.load_source()
    fixture = topup.seed_fixture(apps, private_work, raw, source_contract=source_contract)
    with disable_auditlog(), transaction.atomic():
        for course, payment in source['course_payments'].items():
            CourseSubscription._base_manager.filter(pk=source['course_subscriptions'][course]['id']).update(
                payment_id=source['payments'][payment]['id'])
        for row in source['installments'].values():
            CourseSubscriptionInstallment._base_manager.bulk_create([CourseSubscriptionInstallment(
                course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['course_subscription']]['id'],
                id=row['number'], amount=Decimal('10.00'), paid=row['paid'], payment_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
                payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None)])
            fixture['created'].append((CourseSubscriptionInstallment, row['id']))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with topup.fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt, source_contract=contract, seed=seed_fixture) as fixture:
        yield fixture


PAYMENT, INSTALLMENT, COURSE_SUBSCRIPTION = topup.PAYMENT, 'application.CourseSubscriptionInstallment', 'application.CourseSubscription'
AUDIT, INDEX = topup.AUDIT, topup.INDEX
MANAGED = (*topup.MANAGED, INSTALLMENT, COURSE_SUBSCRIPTION)
plain = topup.plain


def deleted_changes(model, row):
    """auditlog's delete diff: every stored field against None, None-valued
    fields kept only for foreign keys and DateTimeFields ('None') and JSON
    fields ('null')."""
    changes = {}
    for field in model._meta.concrete_fields:
        value = getattr(row, field.attname)
        if value is None:
            if field.is_relation or field.get_internal_type() == 'DateTimeField':
                changes[field.name] = ['None', 'None']
            elif field.get_internal_type() == 'JSONField':
                changes[field.name] = ['null', 'None']
            continue
        if field.get_internal_type() == 'DateTimeField':
            value = topup.auditlog_moment(value)
        elif field.get_internal_type() == 'JSONField':
            value = json.dumps(value, sort_keys=True)
        changes[field.name] = [str(value), 'None']
    return changes


def without(rows, key, ids):
    return [r for r in rows if plain(r[key]) not in ids]


def assert_effect(case, before, after, source, remote, fixture, window, rows):
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    tables = {'payment': source['payments'], 'installment': source['installments']}
    gone = {kind: {tables[kind][label]['id'] for k, label, _ in effect['deleted'] if k == kind} for kind in tables}
    contract.require(after[INSTALLMENT] == without(before[INSTALLMENT], 'course_subscription_installment_id', gone['installment']),
                     'state.installments')
    cleared = [r for r in before[COURSE_SUBSCRIPTION] if plain(r['payment_id']) in gone['payment']]
    expected_courses = [dict(r, payment_id=None) if r in cleared else r for r in before[COURSE_SUBSCRIPTION]]
    contract.require(after[COURSE_SUBSCRIPTION] == expected_courses, 'state.course-subscriptions')
    kept_payments = without(before[PAYMENT], 'payment_id', gone['payment'])
    contract.require(all(r in after[PAYMENT] for r in kept_payments) and not any(plain(r['payment_id']) in gone['payment'] for r in after[PAYMENT]),
                     'state.payments')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    added = sorted((new_a[k] for k in set(new_a) - set(old_a)), key=lambda r: int(r['id']))
    contract.require(len(added) >= len(effect['deleted']), 'audit.count')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = source['ids'][effect['actor']]
    names = {'payment': ('payment', 'Payment'), 'installment': ('coursesubscriptioninstallment', 'CourseSubscriptionInstallment')}
    for event, (kind, label, index) in zip(added, effect['deleted']):
        model, name = names[kind]
        key = tables[kind][label]['id']
        contract.require(event['content_type_id'] == types[model] and event['action'] == 2 and event['object_pk'] == key
                         and event['object_repr'] == f'{name} object ({key})', 'audit.delete-identity:' + label)
        contract.require(event['changes'] == rows[key], 'audit.delete-changes:' + label)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        row = indexes.pop(str(event['id']), None)
        if index is None:
            contract.require(row is None, 'index.absent:' + label)
        else:
            contract.require(row is not None and row['sport_association_id'] == index[0] and row['resolution_path'] == index[1],
                             'index.delete:' + label)
    deleted_ids = {str(r['id']) for r in added[:len(effect['deleted'])]}
    rest = dict(after)
    rest[AUDIT] = [r for r in after[AUDIT] if str(plain(r['id'])) not in deleted_ids]
    rest[INDEX] = [r for r in after[INDEX] if str(plain(r['log_entry_id'])) not in deleted_ids]
    base = dict(before)
    base[PAYMENT] = kept_payments
    if effect['link']:
        assign.assert_link({'effect': {'kind': 'link', 'holding': effect['link'][0], 'course': effect['link'][1]}}, base, rest, source)
    elif effect['create']:
        assign.assert_create({**case, 'effect': {'kind': 'create', **effect['create']}}, base, rest, source, remote, fixture, window)
    else:
        contract.require(all(base[k] == rest[k] for k in topup.MANAGED), 'state.assign-no-effect')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from application.models import CourseSubscriptionInstallment, Payment
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    contract.require(fixture['baseline_state'] == scopes.snapshot_state(apps), 'state.changed')
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        rows = {str(r.pk): deleted_changes(Payment, r) for r in Payment._base_manager.filter(pk__in=[p['id'] for p in source['payments'].values()])}
        rows.update({str(r.pk): deleted_changes(CourseSubscriptionInstallment, r) for r in CourseSubscriptionInstallment._base_manager.filter(
            pk__in=[i['id'] for i in source['installments'].values()])})
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            start = datetime.now(timezone.utc)
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            window = (start, datetime.now(timezone.utc))
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture, window, rows)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
