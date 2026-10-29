"""Parent-only superuser association-list HTTP runner; imports are inert.

Seeds the shared identity fixture, pins every active association's listed
values to the authored ones (audit disabled) and verifies that no case
changes any model state.
"""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from TESTS.contracts import test_association_list_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/association_list_http.json', 'TESTS/contracts/test_association_list_http.py',
           'TESTS/adapters/django/association_list_http.py', 'BE/application/views/profile_views.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/impersonation.py',
           'BE/application/models/user_models.py', 'BE/application/models/billing_models.py')


def moment(text):
    return None if text is None else datetime.fromisoformat(text.replace('Z', '+00:00'))


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import BillingPlan, BillingSubscription, SportAssociation, User, UsersOnboarding
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    ids = source['ids']
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        active = {str(pk) for pk in SportAssociation.objects.values_list('pk', flat=True)}
        contract.require(active == {ids[label] for label in source['associations']}, 'fixture.active-associations')
        for label, spec in source['associations'].items():
            SportAssociation._base_manager.filter(pk=ids[label]).update(**spec['association'])
            user = dict(spec['user'], date_joined=moment(spec['user']['date_joined']), last_login=moment(spec['user']['last_login']))
            User._base_manager.filter(pk=ids[spec['owner']]).update(**user)
        created = fixture['created']
        for values in source['onboarding']:
            row = UsersOnboarding(users_onboarding_id=values['id'], user_id=ids[values['user']],
                                  **{k: v for k, v in values.items() if k not in ('id', 'user')})
            UsersOnboarding._base_manager.bulk_create([row])
            created.append((UsersOnboarding, row.pk))
        BillingPlan._base_manager.bulk_create([BillingPlan(billing_plan_id=source['plan'], name='Piano')])
        created.append((BillingPlan, source['plan']))
        for values in source['billing']:
            BillingSubscription._base_manager.bulk_create([BillingSubscription(
                billing_subscription_id=values['id'], user_id=ids[values['user']], auto_renewal=values['auto_renewal'],
                renewal_type=values['renewal_type'], ends_on=moment(values['ends_on']), billing_plan_id=values['plan'])])
            created.append((BillingSubscription, values['id']))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def remove_rows():
            if fixture is not None:
                shared.delete_created(fixture)
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    results = []
    for case in source['cases']:
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
