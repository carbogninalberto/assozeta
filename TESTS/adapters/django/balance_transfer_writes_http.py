"""Parent-only balance-sheet account transfer add, update and delete HTTP runner; imports are inert.

Seeds the accounts-list rows (audit disabled) and verifies each case against
the complete model state: an add creates one transfer of the owner's
association with one auditlog create entry; an update changes only the named
fields with one update entry when something changed (both indexed
directly); a delete removes the transfer with one delete entry indexed
through changes_json. New transfers are removed before the fixture cleanup
compares the model state.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import ipaddress
from pathlib import Path

from TESTS.contracts import test_balance_transfer_writes_http as contract
from TESTS.adapters.django import balance_accounts_http as accounts
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/balance_transfer_writes_http.json', 'TESTS/contracts/test_balance_transfer_writes_http.py',
           'TESTS/adapters/django/balance_transfer_writes_http.py', 'TESTS/adapters/django/balance_accounts_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/balance_sheet_views.py', 'BE/application/serializers/balance_sheet.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/permissions.py', 'BE/application/audit_resolvers.py')
TRANSFER, AUDIT, INDEX = 'application.CustomAccountsTransfer', 'auditlog.LogEntry', 'application.AuditLogIndex'
MANAGED = (TRANSFER, AUDIT, INDEX)


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        accounts.seed_rows(contract.load_source(), fixture['created'])
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models.balance_sheet_models import CustomAccountsTransfer
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    original = list(CustomAccountsTransfer._base_manager.values_list('pk', flat=True))
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
                    CustomAccountsTransfer._base_manager.exclude(pk__in=[*original, *seeded]).delete()
                    shared.delete_created(fixture)
            carnets.state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, source, remote, fixture, window, rows):
    from application.models.balance_sheet_models import CustomAccountsTransfer
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    old_t, new_t = keyed(before[TRANSFER], 'custom_account_transfer_id'), keyed(after[TRANSFER], 'custom_account_transfer_id')
    action, path = {'add': (0, 'direct'), 'update': (1, 'direct'), 'delete': (2, 'changes_json.sport_association')}[effect['kind']]
    if effect['kind'] == 'delete':
        key = source['transfers'][effect['label']]['id']
        contract.require(set(new_t) == set(old_t) - {key} and all(new_t[k] == old_t[k] for k in new_t), 'state.deleted')
        changes = rows[key]
    else:
        if effect['kind'] == 'add':
            keys = set(new_t) - set(old_t)
            contract.require(len(keys) == 1 and all(new_t[k] == old_t[k] for k in old_t), 'state.created')
            key = keys.pop()
        else:
            key = source['transfers'][effect['label']]['id']
            contract.require(set(new_t) == set(old_t) and all(new_t[k] == old_t[k] for k in old_t if k != key), 'state.kept')
        row = CustomAccountsTransfer._base_manager.get(pk=key)
        view = effect['transfer']
        moment = row.date.astimezone(timezone.utc)
        if view['date'] == '$now':
            contract.require(window[0] <= row.date <= window[1], 'state.default-date')
        else:
            contract.require(moment == datetime.fromisoformat(view['date'].replace('Z', '+00:00')), 'state.date')
        contract.require((str(row.custom_account_from_id) if row.custom_account_from_id else None) == view['custom_account_from']
                         and (str(row.custom_account_to_id) if row.custom_account_to_id else None) == view['custom_account_to']
                         and row.amount == Decimal(view['amount']) and str(row.sport_association_id) == own, 'state.transfer')
        changes = effect.get('changes')
        if effect['kind'] == 'add':
            changes = topup.created_changes(CustomAccountsTransfer, row, {'date': topup.auditlog_moment(row.date)})
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(all(new_a[k] == old_a[k] for k in old_a) and all(new_i[k] == old_i[k] for k in old_i), 'audit.kept')
    contract.require(len(added) == (1 if changes else 0) and len(indexes) == len(added), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    for event in added:
        contract.require(event['content_type_id'] == types['customaccountstransfer'] and event['action'] == action
                         and event['object_pk'] == key and event['object_repr'] == f'CustomAccountsTransfer object ({key})', 'audit.identity')
        contract.require(event['changes'] == changes, 'audit.changes')
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes[str(event['id'])]
        contract.require(index['sport_association_id'] == own and index['resolution_path'] == path, 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from application.models.balance_sheet_models import CustomAccountsTransfer
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        rows = {str(r.pk): replace.deleted_changes(CustomAccountsTransfer, r) for r in CustomAccountsTransfer._base_manager.filter(
            pk__in=[t['id'] for t in source['transfers'].values()])}
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
