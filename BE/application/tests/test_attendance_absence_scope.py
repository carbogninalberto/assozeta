"""Prepared regressions against the real absence handler, without mocked writes."""
from django.utils import timezone
from application.models import User, AttendanceRegistry, AttendanceDay
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_course,
    create_test_subscription, create_test_course_subscription, create_test_associate,
)


class AttendanceAbsenceScopeTests(BaseAPITestCase):
    def setup_day(self, association, subscription=None):
        course = create_test_course(sport_association=association)
        registry = AttendanceRegistry.objects.create(course=course, status=AttendanceRegistry.PUBLISHED, events=[])
        day = AttendanceDay.objects.create(attendance_registry=registry, title='Lezione assenza',
            date=timezone.now(), attendees=[], expected_absences=[])
        subscription = subscription or create_test_subscription(sport_association=association)
        enrollment = create_test_course_subscription(course=course, subscription=subscription)
        return day, enrollment

    def mark(self, day, enrollment, absent=True):
        return self.client.post(f'/attendance-day/{day.pk}/mark-absent',
            {'course_subscription_id': str(enrollment.pk), 'absent': absent}, format='json')

    def assert_empty(self, *days):
        for day in days:
            day.refresh_from_db()
            self.assertEqual(day.attendees, [])
            self.assertEqual(day.expected_absences, [])

    def test_owner_cannot_write_foreign_date_or_another_course_enrollment(self):
        own, own_enrollment = self.setup_day(self.sport_association)
        other_course, other_enrollment = self.setup_day(self.sport_association)
        foreign, foreign_enrollment = self.setup_day(create_test_sport_association())
        self.assertEqual(self.mark(foreign, foreign_enrollment).status_code, 404)
        self.assertEqual(self.mark(own, other_enrollment).status_code, 403)
        self.assertEqual(self.mark(own, foreign_enrollment).status_code, 403)
        self.assert_empty(own, other_course, foreign)
        self.assertEqual(self.mark(own, own_enrollment).status_code, 200)

    def test_ordinary_athlete_can_toggle_only_own_subscription(self):
        athlete = create_test_user(role=User.ATHLETE)
        own, enrolled = self.setup_day(self.sport_association,
            create_test_subscription(sport_association=self.sport_association, user=athlete))
        other = create_test_subscription(sport_association=self.sport_association)
        other_enrolled = create_test_course_subscription(course=enrolled.course, subscription=other)
        foreign, foreign_enrolled = self.setup_day(create_test_sport_association())
        self.client.force_authenticate(user=athlete)
        self.assertEqual(self.mark(own, other_enrolled).status_code, 403)
        self.assertEqual(self.mark(foreign, foreign_enrolled).status_code, 404)
        self.assert_empty(own, foreign)
        self.assertEqual(self.mark(own, enrolled).status_code, 200)
        own.refresh_from_db()
        self.assertEqual(own.expected_absences, [{'course_subscription_id': str(enrolled.pk)}])
        self.assertEqual(self.mark(own, enrolled, False).status_code, 200)
        own.refresh_from_db()
        self.assertEqual(own.expected_absences, [])
        self.assertEqual(own.attendees, [{'course_subscription_id': str(enrolled.pk)}])

    def test_same_person_visibility_is_scoped_and_blank_codes_do_not_grant_access(self):
        athlete = create_test_user(role=User.ATHLETE)
        shared = 'BNCGLL96C50H501Z'
        owned_person = create_test_associate(sport_association=self.sport_association, tax_code=shared)
        today = timezone.now().date()
        create_test_subscription(sport_association=self.sport_association, associate=owned_person, user=athlete,
            start_date=today.replace(year=today.year-2, month=1, day=1),
            end_date=today.replace(year=today.year-2, month=12, day=31))
        # A fiscal code is unique among active people in one association.
        # Another registration for the same real person exercises the fallback
        # without constructing an impossible duplicate person.
        family, enrolled = self.setup_day(self.sport_association,
            create_test_subscription(sport_association=self.sport_association, associate=owned_person))
        foreign_association = create_test_sport_association()
        foreign_person = create_test_associate(sport_association=foreign_association, tax_code=shared)
        foreign, foreign_enrolled = self.setup_day(foreign_association,
            create_test_subscription(sport_association=foreign_association, associate=foreign_person))
        blank_person = create_test_associate(sport_association=self.sport_association, tax_code='')
        blank, blank_enrolled = self.setup_day(self.sport_association,
            create_test_subscription(sport_association=self.sport_association, associate=blank_person))
        self.client.force_authenticate(user=athlete)
        self.assertEqual(self.mark(family, enrolled).status_code, 200)
        self.assertEqual(self.mark(foreign, foreign_enrolled).status_code, 404)
        self.assertEqual(self.mark(blank, blank_enrolled).status_code, 403)
        self.assert_empty(foreign, blank)

    def test_read_only_collaborator_cannot_mutate_owned_or_foreign_dates(self):
        own, enrolled = self.setup_day(self.sport_association)
        foreign, foreign_enrolled = self.setup_day(create_test_sport_association())
        reader = create_test_user(role=User.COLLABORATOR, connected_user=self.user,
            collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.courses.attendance.read'])
        self.client.force_authenticate(user=reader)
        for day, member in [(own, enrolled), (foreign, foreign_enrolled)]:
            self.assertEqual(self.mark(day, member).status_code, 403)
        self.assert_empty(own, foreign)
