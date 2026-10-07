"""Actual DELETE handler must preserve foreign association attendance rows.

Prepared integration regressions; these do not establish browser execution.
"""
from django.utils import timezone
from application.models import User, AttendanceRegistry, AttendanceDay
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_course,
)


class AttendanceDayDeletionScopeTests(BaseAPITestCase):
    def day(self, association):
        course = create_test_course(sport_association=association)
        registry = AttendanceRegistry.objects.create(course=course, events=[], status=AttendanceRegistry.PUBLISHED)
        return AttendanceDay.objects.create(attendance_registry=registry, title='Empty lesson',
                                            date=timezone.now(), attendees=[], expected_absences=[])

    def test_owner_can_delete_only_the_owned_date(self):
        own = self.day(self.sport_association)
        foreign = self.day(create_test_sport_association())
        response = self.client.delete(f'/attendance-day/{own.pk}/delete')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'data': {'msg': 'attendance day deleted'}})
        self.assertFalse(AttendanceDay.objects.filter(pk=own.pk).exists())
        self.assertTrue(AttendanceDay.objects.filter(pk=foreign.pk).exists())

    def test_foreign_id_returns_not_found_and_preserves_both_rows(self):
        own = self.day(self.sport_association)
        foreign = self.day(create_test_sport_association())
        response = self.client.delete(f'/attendance-day/{foreign.pk}/delete')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(AttendanceDay.objects.filter(pk=foreign.pk).exists())
        self.assertTrue(AttendanceDay.objects.filter(pk=own.pk).exists())

    def test_read_only_collaborator_denied_for_own_and_foreign_rows(self):
        own = self.day(self.sport_association)
        foreign = self.day(create_test_sport_association())
        reader = create_test_user(role=User.COLLABORATOR, connected_user=self.user,
                                  collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                                  collaborator_permissions=['association.courses.attendance.read'])
        self.client.force_authenticate(user=reader)
        for row in (own, foreign):
            response = self.client.delete(f'/attendance-day/{row.pk}/delete')
            self.assertEqual(response.status_code, 403)
            self.assertTrue(AttendanceDay.objects.filter(pk=row.pk).exists())
