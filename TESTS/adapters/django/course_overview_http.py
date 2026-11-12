"""Parent-only course/<uid>/overview HTTP runner; imports are inert.

Seeds tags, locations and courses of both associations (audit disabled). The
route is a read: every case must leave the complete model state unchanged.
"""
from datetime import datetime
from decimal import Decimal

from TESTS.contracts import test_course_overview_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/course_overview_http.json', 'TESTS/contracts/test_course_overview_http.py',
           'TESTS/adapters/django/course_overview_http.py', 'BE/application/views/course_views.py',
           'BE/application/serializers/courses_serializers.py', 'BE/application/models/courses_models.py',
           'BE/application/mixin.py') + harness.SOURCES


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


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
