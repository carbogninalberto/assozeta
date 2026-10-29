"""Parent-only course/<uid>/overview HTTP runner; imports are inert.

Seeds tags, locations and courses of both associations (audit disabled). The
route is a read: every case must leave the complete model state unchanged.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from TESTS.contracts import test_course_overview_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/course_overview_http.json', 'TESTS/contracts/test_course_overview_http.py',
           'TESTS/adapters/django/course_overview_http.py', 'BE/application/views/course_views.py',
           'BE/application/serializers/courses_serializers.py', 'BE/application/models/courses_models.py', 'BE/application/mixin.py')


def moment(value):
    return None if value is None else datetime.fromisoformat(value.replace('Z', '+00:00'))


def seed_rows(source, created):
    from application.models import Course
    from application.models.courses_models import CourseLocation, CourseTags
    ids = source['ids']
    for row in source['tags'].values():
        CourseTags._base_manager.bulk_create([CourseTags(tag_id=row['id'], tag_name=row['name'], sport_association_id=row['association'],
                                                         creation_date=moment('2026-08-01T00:00:00Z'))])
        created.append((CourseTags, row['id']))
    for row in source['locations'].values():
        CourseLocation._base_manager.bulk_create([CourseLocation(course_location_id=row['id'], sport_association_id=row['association'],
                                                                 title=row['title'], address=row['address'])])
        created.append((CourseLocation, row['id']))
    for row in source['courses'].values():
        Course._base_manager.bulk_create([Course(course_id=row['id'], sport_association_id=row['association'], status_flag=row['status'],
            course_type=row['type'], creation_date=moment(row['created']), title=row['title'], description=row['description'],
            multi_payments=row['multi_payments'], fee=None if row['fee'] is None else Decimal(row['fee']), events=row['events'],
            one_fee_payment=row['one_fee_payment'], one_fee=Decimal(row['one_fee']), google_calendar_id=row['google_calendar_id'],
            google_background_color=row['color'], multiple_quotes=row['multiple_quotes'], start_date=moment(row['start']),
            end_date=moment(row['end']), pinned=row['pinned'], billed_duration_is_sport_season=row['season'],
            billed_frequency=row['frequency'], billed_from_subscription_date=row['from_subscription'],
            billed_from_day_of_month=row['day'], auto_renewal=row['renewal'], deleted=row['deleted'],
            group_id=ids[row['group']] if row['group'] else None)])
        created.append((Course, row['id']))
        Course.tags.through.objects.bulk_create([Course.tags.through(course_id=row['id'], coursetags_id=source['tags'][t]['id'])
                                                 for t in row['tags']])
        Course.locations.through.objects.bulk_create([Course.locations.through(course_id=row['id'],
                                                      courselocation_id=source['locations'][l]['id']) for l in row['locations']])


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
            carnets.state_unchanged(before, scopes.snapshot_state(apps))
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
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results = []
    for case in source['cases']:
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
