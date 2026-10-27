"""Parent-only testimonial add HTTP runner; imports are inert.

Seeds the shared identity fixture with every association unreviewed (audit
disabled), runs the authored sequence, and verifies each case against the
complete model state: only one authored Testimonial row, the authored
``reviewed`` flip, one exact audit entry and one audit index row may change.
"""
from contextlib import contextmanager
import ipaddress
import time
from pathlib import Path

from TESTS.contracts import test_testimonials_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/testimonials_http.json', 'TESTS/contracts/test_testimonials_http.py',
           'TESTS/contracts/test_workflow_writes_http.py', 'TESTS/adapters/django/testimonials_http.py',
           'TESTS/adapters/django/workflow_writes_http.py', 'BE/application/views/profile_views.py',
           'BE/application/models/user_models.py', 'BE/application/audit_resolvers.py',
           'BE/application/permissions_registry.py')
TESTIMONIAL, ASSOCIATION = 'application.Testimonial', 'application.SportAssociation'
AUDIT, INDEX = 'auditlog.LogEntry', 'application.AuditLogIndex'


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import SportAssociation
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        SportAssociation._base_manager.update(reviewed=False)
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import SportAssociation, Testimonial
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    reviewed = {key: row['reviewed'] for key, row in keyed(before[ASSOCIATION], 'sport_association_id').items()}
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
                Testimonial._base_manager.exclude(pk__in=list(keyed(before[TESTIMONIAL], 'testimonial_id'))).delete()
                for key, value in reviewed.items():
                    SportAssociation._base_manager.filter(pk=key).update(reviewed=value)
                if fixture is not None:
                    shared.delete_created(fixture)
                LogEntry.objects.exclude(pk__in=original_audit).delete()
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, source, remote, sessions):
    managed = (TESTIMONIAL, ASSOCIATION, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    ids = source['ids']
    effect = case['effect']
    old_t, new_t = keyed(before[TESTIMONIAL], 'testimonial_id'), keyed(after[TESTIMONIAL], 'testimonial_id')
    old_s, new_s = keyed(before[ASSOCIATION], 'sport_association_id'), keyed(after[ASSOCIATION], 'sport_association_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    expected = effect['testimonial']
    if expected is None:
        contract.require(old_t == new_t, 'state.no-testimonial')
    else:
        added = set(new_t) - set(old_t)
        contract.require(len(added) == 1 and {k: new_t[k] for k in old_t if k in new_t} == old_t and set(old_t) <= set(new_t),
                         'state.one-testimonial')
        row = new_t[added.pop()]
        contract.require(row['sport_association_id'] == ids[expected['association']], 'state.testimonial-association')
        contract.require(row['text'] == expected['text'], 'state.testimonial-text')
        contract.require(type(row['score']) is float and row['score'] == expected['score'], 'state.testimonial-score')
    flip = effect['reviewed']
    changed = {key for key in set(old_s) | set(new_s) if old_s.get(key) != new_s.get(key)}
    if flip is None:
        contract.require(not changed and old_a == new_a and old_i == new_i, 'state.no-review')
        return
    target = ids[flip['association']]
    contract.require(changed == {target} and old_s[target]['reviewed'] is False and new_s[target]['reviewed'] is True
                     and {k: v for k, v in new_s[target].items() if k != 'reviewed'} == {k: v for k, v in old_s[target].items() if k != 'reviewed'},
                     'state.reviewed-only')
    added_a = set(new_a) - set(old_a)
    contract.require(len(added_a) == 1 and set(old_a) <= set(new_a) and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event = new_a[added_a.pop()]
    users = {str(scalar(u['user_id'])): u for u in before['application.User']}
    actor = ids[flip['actor']]
    content_types = [ct for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'sportassociation']
    contract.require(len(content_types) == 1, 'audit.content-type')
    contract.require(event['content_type_id'] == content_types[0]['id'] and event['object_pk'] == target
                     and event['object_repr'] == f'SportAssociation object ({target})' and event['action'] == 1, 'audit.identity')
    contract.require(event['changes'] == flip['changes'], 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    if flip['impersonation']:
        target_user = case['headers']['User-Id']
        additional = {'impersonation_session': sessions[flip['impersonation']], 'actor_id': actor,
                      'target_user_id': target_user, 'association_id': None}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = set(new_i) - set(old_i)
    contract.require(len(added_i) == 1 and all(new_i[k] == old_i[k] for k in old_i), 'index.one-row')
    index = new_i[added_i.pop()]
    contract.require(index['log_entry_id'] == event['id'] and index['sport_association_id'] == target
                     and index['resolution_path'] == source['index_path'], 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], bindings={})
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture['sessions'])
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
