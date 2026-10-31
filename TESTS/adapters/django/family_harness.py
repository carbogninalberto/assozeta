"""Shared parent-only Django runner harness for authored HTTP parity families; imports are inert.

A family adapter supplies its contract, a seed function and, for writes, an
effect check; this module provides the driver interface
(`fixture_context`, `run_workload`) with the same guarantees every family
runner has had:
- the fixture is seeded with auditlog disabled inside one transaction, and the
  complete model state, historical read cache and sequences are restored and
  verified afterwards (a cleanup receipt that is not verified fails the run);
- every case is attributed to exactly one request of the backend under test;
- a read family's cases must leave the complete model state unchanged;
- a write family's effect check sees the complete state before and after.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

AUDIT, INDEX = 'auditlog.LogEntry', 'application.AuditLogIndex'
SOURCES = ('TESTS/adapters/django/family_harness.py', 'TESTS/contracts/http_family.py')


def fixture_context_for(contract, seed, *, purge=()):
    """The driver's fixture_context.

    seed(source, created) inserts the family rows and appends (Model, pk) to
    created. purge: zero-argument callables returning models whose rows the run
    may create; rows that are neither original nor seeded are deleted before the
    final state comparison.
    """
    def seed_fixture(apps, private_work, raw):
        from auditlog.context import disable_auditlog
        from django.db import transaction
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        with disable_auditlog(), transaction.atomic():
            fixture = scopes.seed_fixture(apps, Path(private_work), raw)
            seed(contract.load_source(), fixture['created'])
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
        original = {model: list(model._base_manager.values_list('pk', flat=True)) for model in (load() for load in purge)}
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
                        seeded = [pk for _, pk in fixture['created']]
                        for model, keep in original.items():
                            model._base_manager.exclude(pk__in=[*keep, *seeded]).delete()
                        shared.delete_created(fixture)
                carnets.state_unchanged(before, scopes.snapshot_state(apps))
                return True
            receipt.update(scopes.cleanup_steps((
                ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
                ('sequences', lambda: mutations.restore_sequences(sequences_before)),
                ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
            )))
            contract.require(receipt['verified'], 'cleanup.fixture-incomplete')

    return fixture_context


def run_workload_for(contract, effect=None, *, prepare=None):
    """The driver's run_workload.

    effect(case, before, after, source, context) checks a write family's case;
    without it every case must leave the complete state unchanged. prepare(source)
    runs before each case and its result is context['prepared'] (for example
    rows captured before a delete). context['remote'] is shared across cases.
    """
    def run_workload(label, backend, base_url, fixture, apps, raw, observer):
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        source = contract.load_source()
        carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
        results, context = [], {'remote': {}, 'fixture': fixture}
        for case in source['cases']:
            before = scopes.snapshot_state(apps) if effect else None
            context['prepared'] = prepare(source) if prepare else None
            cursor = observer.cursor()
            token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
            try:
                status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
                if effect:
                    after = scopes.snapshot_state(apps)
                    shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
                    effect(case, before, after, source, context)
                else:
                    shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
                    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
            except BaseException as error:
                error.case = f'{label}:{case["name"]}'
                raise
            results.append({'case': case['name'], 'status': status})
        shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
        return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}

    return run_workload


def require_unmanaged_unchanged(contract, before, after, managed):
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed},
                     'state.other-models')


def audit_delta(contract, before, after):
    """New auditlog entries and their index rows; existing ones must be unchanged."""
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    contract.require(all(new_a[k] == old_a[k] for k in old_a) and all(new_i[k] == old_i[k] for k in old_i), 'audit.kept')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    return added, indexes


def check_audit_event(contract, case, event, indexes, *, before, ids, model, key, action, changes, actor, association, path, context):
    """One auditlog entry of the application model class `model` for `key`, with its index row."""
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor_id = ids[actor]
    contract.require(event['content_type_id'] == types[model.lower()] and event['action'] == action and event['object_pk'] == key
                     and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == actor_id and event['actor_email'] == users[actor_id]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes[str(event['id'])]
    contract.require(index['sport_association_id'] == association and index['resolution_path'] == path, 'index.fields')


def pairs(rows, left, right):
    """{(left, right)} of M2M through rows, as strings."""
    return {(str(scalar(row[left])), str(scalar(row[right]))) for row in rows}
