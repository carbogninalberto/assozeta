"""Parent-only carnet/<uid>/unassign HTTP runner; imports are inert.

Seeds the carnet-replace rows (courses, installments, payments; audit disabled) and
verifies each case against the complete model state: only the target carnet
subscription, its course links, an unpaid payment and the installments of that
payment may disappear, with one auditlog delete entry per delete in order (the payment
signal deletes the installments first: payment, installments, payment again); the
payment entry must also be identical across backends.
"""
from contextlib import contextmanager
import ipaddress

from TESTS.contracts import test_carnet_unassign_http as contract
from TESTS.adapters.django import carnet_replace_http as replaces
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/carnet_unassign_http.json', 'TESTS/contracts/test_carnet_unassign_http.py',
           'TESTS/adapters/django/carnet_unassign_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/carnet_assign_http.py', 'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'TESTS/adapters/django/carnet_list_http.py', 'BE/application/views/carnet_views.py', 'BE/application/models/carnet_models.py',
           'BE/application/signals.py', 'BE/application/audit_resolvers.py')
PAYMENT, HOLDING, LINK = 'application.Payment', 'application.CarnetSubscription', 'application.CarnetSubscription_course_subscription'
INSTALLMENT = replaces.INSTALLMENT
AUDIT, INDEX = 'auditlog.LogEntry', 'application.AuditLogIndex'
PAYMENT_CHANGES = {}


def seed_fixture(apps, private_work, raw):
    return replaces.seed_fixture(apps, private_work, raw, source_contract=contract)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with topup.fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt, source_contract=contract,
                               seed=replaces.seed_fixture) as fixture:
        yield fixture


def assert_effect(case, before, after, source, remote, fixture, rows):
    managed = (PAYMENT, HOLDING, LINK, AUDIT, INDEX, 'application.Payment_attachments', INSTALLMENT)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in managed), 'state.no-effect')
        return
    plain = lambda value: value.get('value') if isinstance(value, dict) else value
    holding = source['carnet_subscriptions'][effect['holding']]['id']
    contract.require(after[HOLDING] == [r for r in before[HOLDING] if plain(r['carnet_subscription_id']) != holding], 'state.holding')
    contract.require(after[LINK] == [r for r in before[LINK] if plain(r['carnetsubscription_id']) != holding], 'state.links')
    contract.require(after[PAYMENT] == [r for r in before[PAYMENT] if plain(r['payment_id']) != effect['payment']], 'state.payment')
    gone = set(effect['installments'])
    contract.require(after[INSTALLMENT] == [r for r in before[INSTALLMENT] if plain(r['course_subscription_installment_id']) not in gone],
                     'state.installments')
    added = sorted((new_a[k] for k in set(new_a) - set(old_a)), key=lambda r: int(r['id']))
    contract.require(len(added) == len(effect['entries']) and all(new_a[k] == old_a[k] for k in old_a), 'audit.entries')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = source['ids'][effect['actor']]
    added_i = {str(row['log_entry_id']): row for row in (new_i[k] for k in set(new_i) - set(old_i))}
    contract.require(all(new_i[k] == old_i[k] for k in old_i), 'index.existing')
    expected_index = 0
    names = {'payment': 'Payment', 'carnetsubscription': 'CarnetSubscription', 'coursesubscriptioninstallment': 'CourseSubscriptionInstallment'}
    for event, entry in zip(added, effect['entries']):
        contract.require(event['object_pk'] == entry['id'] and event['content_type_id'] == types[entry['model']] and event['action'] == 2
                         and event['object_repr'] == f"{names[entry['model']]} object ({entry['id']})", 'audit.identity')
        if 'changes' in entry:
            contract.require(event['changes'] == entry['changes'], 'audit.changes:' + entry['model'])
        elif entry.get('rendered'):
            contract.require(event['changes'] == rows[entry['id']], 'audit.rendered:' + entry['model'])
        else:
            contract.require(all(event['changes'].get(k) == v for k, v in entry['values'].items()), 'audit.values:' + entry['model'])
            contract.require(PAYMENT_CHANGES.setdefault(case['name'], event['changes']) == event['changes'], 'audit.payment-differs')
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = added_i.get(str(event['id']))
        if entry['index'] is None:
            contract.require(index is None, 'index.absent')
        else:
            expected_index += 1
            contract.require(index is not None and index['sport_association_id'] == entry['index'][0]
                             and index['resolution_path'] == entry['index'][1], 'index.fields:' + entry['model'])
    contract.require(len(added_i) == expected_index, 'index.count')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from application.models import CourseSubscriptionInstallment
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    contract.require(fixture['baseline_state'] == scopes.snapshot_state(apps), 'state.changed')
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        rows = {str(r.pk): replaces.deleted_changes(CourseSubscriptionInstallment, r) for r in CourseSubscriptionInstallment._base_manager.filter(
            pk__in=[i['id'] for i in source['installments'].values()])}
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
