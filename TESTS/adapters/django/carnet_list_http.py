"""Parent-only carnet/list HTTP runner; imports are inert.

Seeds carnets, associates, subscriptions, a course with a live and a
soft-deleted course subscription, a payment and carnet subscriptions (audit
disabled, creation dates set after insert because they are auto_now_add).
The route is a read: every case must leave the complete model state unchanged.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from TESTS.contracts import test_carnet_list_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/carnet_list_http.json', 'TESTS/contracts/test_carnet_list_http.py',
           'TESTS/adapters/django/carnet_list_http.py', 'BE/application/views/carnet_views.py',
           'BE/application/serializers/carnet_serializers.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/carnet_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/impersonation_scope.py')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def day(text):
    return date.fromisoformat(text) if text else None


def state_unchanged(before, after):
    # Associate.born_date snapshots are date objects, which the shared JSON
    # digest cannot encode; compare the complete snapshots directly.
    contract.require(before == after, 'state.changed')


def seed_rows(source, created):
    """Insert the source's carnet rows in dependency order, recording each for cleanup."""
    from application.models import Associate, Course, CourseSubscription, Payment, Subscription
    from application.models.carnet_models import Carnet, CarnetSubscription
    ids = source['ids']
    user = lambda label: ids[label] if label else None
    for row in source['carnets'].values():
        Carnet._base_manager.bulk_create([Carnet(carnet_id=row['id'], sport_association_id=row['association'], title=row['title'],
            description=row['description'], lessons_number=row['lessons'], fee=Decimal(row['fee']), public=row['public'], meta=row['meta'])])
        Carnet._base_manager.filter(pk=row['id']).update(creation_date=moment(row['created']))
        created.append((Carnet, row['id']))
    for row in source['associates'].values():
        Associate._base_manager.bulk_create([Associate(associate_id=row['id'], sport_association_id=row['association'], user_id=user(row['user']),
            born_date=day(row['born_date']), **{k: row[k] for k in ('first_name', 'last_name', 'email', 'sex', 'tax_code', 'address',
                                                                 'address_cap', 'address_city', 'born_city', 'is_minor', 'phone', 'family_role')})])
        created.append((Associate, row['id']))
    for row in source['subscriptions'].values():
        Subscription._base_manager.bulk_create([Subscription(subscription_id=row['id'], sport_association_id=row['association'],
            user_id=user(row['user']), associate_id=source['associates'][row['associate']]['id'],
            start_date=day(row['start']), end_date=day(row['end']), status_flag=Subscription.ACCEPTED)])
        created.append((Subscription, row['id']))
    for row in source['courses'].values():
        Course._base_manager.bulk_create([Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'],
            description=row.get('description', ''), fee=Decimal('50.00'), status_flag=Course.ACTIVE)])
        created.append((Course, row['id']))
    for row in source['course_subscriptions'].values():
        CourseSubscription._base_manager.bulk_create([CourseSubscription(course_subscription_id=row['id'], deleted=row['deleted'],
            course_id=source['courses'][row['course']]['id'], subscription_id=source['subscriptions'][row['subscription']]['id'])])
        created.append((CourseSubscription, row['id']))
    for row in source['payments'].values():
        Payment._base_manager.bulk_create([Payment(payment_id=row['id'], sport_association_id=row['association'], user_id=user(row['user']),
            subject=Payment.COURSE, amount=Decimal(row['amount']), paid=row.get('paid', False),
            creation_date=moment('2026-10-01T08:00:00Z'))])
        created.append((Payment, row['id']))
    for row in source['carnet_subscriptions'].values():
        CarnetSubscription._base_manager.bulk_create([CarnetSubscription(carnet_subscription_id=row['id'],
            carnet_id_id=source['carnets'][row['carnet']]['id'], user_id_id=user(row['user']),
            subscription_id=source['subscriptions'][row['subscription']]['id'] if row['subscription'] else None,
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None, disabled=row['disabled'], meta=row['meta'])])
        CarnetSubscription._base_manager.filter(pk=row['id']).update(creation_date=moment(row['created']))
        CarnetSubscription.course_subscription.through.objects.bulk_create([CarnetSubscription.course_subscription.through(
            carnetsubscription_id=row['id'], coursesubscription_id=source['course_subscriptions'][label]['id']) for label in row['courses']])
        created.append((CarnetSubscription, row['id']))


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        seed_rows(contract.load_source(), fixture['created'])
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
    original_audit = [row['id'] for row in before['auditlog.LogEntry']]
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
            state_unchanged(before, scopes.snapshot_state(apps))
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
    state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results = []
    for case in source['cases']:
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
