"""Parent-only search/profile HTTP runner; imports are inert.

Seeds users, an association, courses, tags, locations, course subscriptions and a
carnet (audit disabled). The embedded association serializer must be
identical across backends. The routes are reads: every case must leave
the complete model state unchanged.
"""
from contextlib import contextmanager
from pathlib import Path

from TESTS.contracts import test_search_profile_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/search_profile_http.json', 'TESTS/contracts/test_search_profile_http.py',
           'TESTS/adapters/django/search_profile_http.py', 'TESTS/adapters/django/carnet_list_http.py',
           'BE/application/views/search_views.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/serializers/courses_serializers.py', 'BE/application/serializers/carnet_serializers.py',
           'BE/application/utils/api_utils.py', 'BE/application/utils/stripe_utils.py', 'BE/instance/integration_configuration.py')
PROFILES = {}


def seed_rows(source, created):
    from datetime import datetime
    from decimal import Decimal
    from application.models import Carnet, Course, CourseSubscription, SportAssociation, User
    from application.models.courses_models import CourseLocation, CourseTags
    moment = lambda text: datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None
    users = source['users']
    for row in users.values():
        User.original_objects.bulk_create([User(user_id=row['id'], username=row['username'], email=row['username'] + '@example.test', role=row['role'],
            password='!fixture', first_name=row['first_name'], last_name=row['last_name'], avatar_image=row['avatar'],
            subscription_start_month=row['month'], subscription_start_day=row['day'], disable_account_creation=row['disable'],
            force_account_creation=row['force'], deleted=row['deleted'])])
        created.append((User, row['id']))
    for row in source['associations'].values():
        SportAssociation._base_manager.bulk_create([SportAssociation(sport_association_id=row['id'], user_id=users[row['user']]['id'],
            **{k: v for k, v in row.items() if k not in ('id', 'user')})])
        created.append((SportAssociation, row['id']))
    club = source['associations']['club']['id']
    for row in source['tags'].values():
        CourseTags._base_manager.bulk_create([CourseTags(tag_id=row['id'], tag_name=row['name'], sport_association_id=club)])
        created.append((CourseTags, row['id']))
    for row in source['locations'].values():
        CourseLocation._base_manager.bulk_create([CourseLocation(course_location_id=row['id'], title=row['title'], address=row['address'],
                                                                 sport_association_id=club)])
        created.append((CourseLocation, row['id']))
    by_label = {}
    for row in source['courses']:
        Course._base_manager.bulk_create([Course(course_id=row['id'], sport_association_id=club, status_flag=row['status'], course_type=row['course_type'],
            creation_date=moment(row['created']), title=row['title'], description=row['description'], fee=Decimal(row['fee']) if row['fee'] else None,
            events=row['events'], one_fee=Decimal(row['one_fee']), google_background_color=row['color'], pinned=row['pinned'],
            start_date=moment(row['start']), end_date=moment(row['end']), deleted=row['deleted'])])
        created.append((Course, row['id']))
        by_label[row['label']] = row['id']
        if row['tag']:
            Course.tags.through.objects.bulk_create([Course.tags.through(course_id=row['id'], coursetags_id=source['tags'][row['tag']]['id'])])
        if row['location']:
            Course.locations.through.objects.bulk_create([Course.locations.through(course_id=row['id'],
                                                                                   courselocation_id=source['locations'][row['location']]['id'])])
    for index, (label, deleted) in enumerate(source['subscriptions']):
        row = CourseSubscription(course_id=by_label[label], deleted=deleted)
        CourseSubscription._base_manager.bulk_create([row])
        created.append((CourseSubscription, row.pk))
    for row in source['carnets'].values():
        Carnet._base_manager.bulk_create([Carnet(carnet_id=row['id'], sport_association_id=club, title=row['title'], description=row['description'],
            lessons_number=row['lessons'], fee=Decimal(row['fee']), public=row['public'])])
        Carnet._base_manager.filter(pk=row['id']).update(creation_date=moment(row['created']))
        created.append((Carnet, row['id']))


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
            if case['name'] in contract.LAST_PROFILE:
                profile = contract.LAST_PROFILE.pop(case['name'])
                contract.require(PROFILES.setdefault(case['name'], profile) == profile, 'response.association-differs')
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
