"""
Course, attendance and instructor-hours lookups resolve rows within the
caller's association: rows of another association behave like unknown ids.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import status

from application.models import User, AttendanceRegistry, Reminders
from application.models.courses_models import CourseSubscription
from application.models.payment_models import Payment
from application.models.user_models import InstructorHours
from application.audit_resolvers import setup_audit_resolvers
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_associate,
    create_test_subscription,
    create_test_course,
    create_test_course_location,
    create_test_payment,
)
from application.tests.test_attendee_views import (
    create_test_event,
    create_test_attendance_registry,
    create_test_attendance_day,
    create_test_reminder,
    create_test_carnet,
)
from application.tests.test_instructor_views import create_test_instructor, create_test_instructor_hours


class OtherAssociationMixin:
    def setUp(self):
        super().setUp()
        self.other_user = create_test_user(role=User.ASSOCIATION)
        self.other_association = create_test_sport_association(user=self.other_user)


class CourseCalendarLookupTests(OtherAssociationMixin, BaseAPITestCase):

    def test_reminder_of_another_association_is_left_alone(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(start_offset_days=2)
        other = create_test_reminder(self.other_user, self.other_association, event)
        event['extendedProps'].update(reminder_enabled=False)

        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event], 'status': AttendanceRegistry.DRAFT,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Reminders.objects.filter(pk=other.pk).exists())

    def test_enabled_reminder_is_created_for_the_own_association(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(start_offset_days=2)
        other = create_test_reminder(self.other_user, self.other_association, event)
        foreign_instructor = create_test_instructor(self.other_user)
        event['extendedProps'].update(reminder_enabled=True, reminder_amount=1, reminder_unit='hours',
                                      instructor={'instructor_id': str(foreign_instructor.instructor_id)})

        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event], 'status': AttendanceRegistry.DRAFT,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        other.refresh_from_db()
        self.assertEqual(other.sport_association, self.other_association)
        own = Reminders.objects.get(sport_association=self.sport_association, event_id=event['event_id'])
        self.assertIsNone(own.instructor)

    def test_own_instructor_is_attached_to_the_reminder(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(start_offset_days=2)
        instructor = create_test_instructor(self.user)
        event['extendedProps'].update(reminder_enabled=True, reminder_amount=1, reminder_unit='hours',
                                      instructor={'instructor_id': str(instructor.instructor_id)})

        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event], 'status': AttendanceRegistry.DRAFT,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        own = Reminders.objects.get(sport_association=self.sport_association, event_id=event['event_id'])
        self.assertEqual(own.instructor, instructor)


class AttendanceLookupTests(OtherAssociationMixin, BaseAPITestCase):

    def setUp(self):
        super().setUp()
        self.other_associate = create_test_associate(sport_association=self.other_association)
        self.other_subscription = create_test_subscription(
            sport_association=self.other_association, associate=self.other_associate)
        self.other_course = create_test_course(sport_association=self.other_association)
        self.other_course_sub = CourseSubscription.objects.create(
            course=self.other_course, subscription=self.other_subscription)
        registry = create_test_attendance_registry(self.other_course)
        create_test_attendance_day(registry, date=timezone.now())

    def test_mark_subscription_of_another_association_is_not_found(self):
        response = self.client.post(f'/attendance/{self.other_subscription.subscription_id}/mark', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data, {'msg': 'Iscrizione non trovata.'})

    def test_mark_own_subscription_checks_in(self):
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(sport_association=self.sport_association, associate=associate)
        course = create_test_course(sport_association=self.sport_association)
        CourseSubscription.objects.create(course=course, subscription=subscription)
        day = create_test_attendance_day(create_test_attendance_registry(course), date=timezone.now())

        response = self.client.post(f'/attendance/{subscription.subscription_id}/mark', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        day.refresh_from_db()
        self.assertEqual(len(day.attendees), 1)

    def test_history_of_another_association_is_not_found(self):
        orphan_course = create_test_course(sport_association=self.other_association)
        CourseSubscription.objects.create(course=orphan_course, subscription=None)

        for path in ('attendance', 'calendar'):
            response = self.client.get(f'/subscription/{self.other_subscription.subscription_id}/{path}')
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, path)

    def test_attendee_of_another_association_keeps_its_carnet(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Lesson')
        day = create_test_attendance_day(create_test_attendance_registry(course, events=[event]), title='Lesson')
        _, other_carnet = create_test_carnet(self.other_association, self.other_subscription,
                                             self.other_course_sub, lessons_left=5)

        response = self.client.post(f'/course/{course.course_id}/attendees/{day.attendance_day_id}/update', {
            'attendees': [{'course_subscription_id': str(self.other_course_sub.course_subscription_id),
                           'associate_name': 'x'}],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        other_carnet.refresh_from_db()
        self.assertEqual(other_carnet.meta['lessons_left'], 5)


class CourseWriteLookupTests(OtherAssociationMixin, BaseAPITestCase):

    def test_update_course_of_another_association_is_denied(self):
        other_course = create_test_course(sport_association=self.other_association, title='Theirs')

        response = self.client.patch(f'/course/{other_course.course_id}/update', {'title': 'Mine'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        other_course.refresh_from_db()
        self.assertEqual(other_course.title, 'Theirs')

    def test_update_course_as_athlete_is_denied(self):
        course = create_test_course(sport_association=self.sport_association, title='Ours')
        self.client.force_authenticate(user=create_test_user(role=User.ATHLETE))

        response = self.client.patch(f'/course/{course.course_id}/update', {'title': 'Mine'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_title_change_only_rewrites_own_payments(self):
        course = create_test_course(sport_association=self.sport_association, title='Yoga Base')
        own = create_test_payment(sport_association=self.sport_association, description='Quota Yoga Base')
        other = create_test_payment(sport_association=self.other_association, description='Quota Yoga Base')

        response = self.client.patch(f'/course/{course.course_id}/update', {'title': 'Yoga Plus'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Payment.objects.get(pk=own.pk).description, 'Quota Yoga Plus')
        self.assertEqual(Payment.objects.get(pk=other.pk).description, 'Quota Yoga Base')

    def test_update_links_only_own_or_already_linked_locations(self):
        course = create_test_course(sport_association=self.sport_association)
        own = create_test_course_location(sport_association=self.sport_association)
        legacy = create_test_course_location(sport_association=None)
        course.locations.add(legacy)
        other = create_test_course_location(sport_association=self.other_association)

        response = self.client.patch(f'/course/{course.course_id}/update', {'locations': [
            {'course_location_id': str(loc.course_location_id)} for loc in (own, legacy, other)
        ]}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(course.locations.all()), {own, legacy})

    def test_add_links_only_own_locations(self):
        own = create_test_course_location(sport_association=self.sport_association)
        other = create_test_course_location(sport_association=self.other_association)

        response = self.client.post('/course/add', {'new_course': {
            'title': 'New', 'description': 'd', 'fee': '10.00', 'course_type': 1,
            'locations': [{'course_location_id': str(own.course_location_id)},
                          {'course_location_id': str(other.course_location_id)}],
        }, 'subscriptions': []}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course = self.sport_association.course_set.get(title='New')
        self.assertEqual(list(course.locations.all()), [own])

    def test_overview_update_without_association_is_denied(self):
        course = create_test_course(sport_association=self.other_association)
        CourseSubscription.objects.create(
            course=course, subscription=create_test_subscription(sport_association=self.other_association))
        subscription = CourseSubscription.objects.get(course=course).subscription
        self.client.force_authenticate(user=create_test_user(role=User.ASSOCIATION))

        response = self.client.post(f'/course/{course.course_id}/overview/{subscription.subscription_id}/update',
                                    {'one_fee_payment': True}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class InstructorHoursLookupTests(OtherAssociationMixin, BaseAPITestCase):

    def setUp(self):
        super().setUp()
        # The registry is filled at startup, before the test database exists.
        setup_audit_resolvers()
        self.instructor = create_test_instructor(self.user)
        self.other_instructor = create_test_instructor(self.other_user)

    def hours_body(self):
        return {'date': timezone.now().date().strftime('%d/%m/%Y'), 'hours': '2.00', 'hourly_billing': '20.00',
                'amount': '40.00', 'compensation_type': 'hourly'}

    def test_add_hours_to_another_association_instructor_is_not_found(self):
        response = self.client.post(f'/instructor/{self.other_instructor.instructor_id}/hours/add',
                                    self.hours_body(), format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(InstructorHours.objects.filter(instructor=self.other_instructor).exists())

    def test_add_hours_to_own_instructor(self):
        response = self.client.post(f'/instructor/{self.instructor.instructor_id}/hours/add',
                                    self.hours_body(), format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(InstructorHours.objects.filter(instructor=self.instructor).exists())

    def test_calculate_ignores_courses_of_another_association(self):
        other_course = create_test_course(sport_association=self.other_association)

        response = self.client.post(f'/instructor/{self.instructor.instructor_id}/hours/calculate',
                                    {'courses': [{'course_id': str(other_course.course_id)}], 'period': '2026/01/01 al 2026/12/31'},
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertFalse(response.data.get('calculation_data'))

        response = self.client.post(f'/instructor/{self.other_instructor.instructor_id}/hours/calculate',
                                    {'courses': [{'course_id': str(other_course.course_id)}], 'period': '2026/01/01 al 2026/12/31'},
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hours_cannot_move_to_another_association_instructor(self):
        hours = create_test_instructor_hours(self.instructor)

        response = self.client.patch(
            f'/instructor/{self.instructor.instructor_id}/hours/{hours.instructor_hours_id}/update',
            {'instructor': str(self.other_instructor.instructor_id)}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        hours.refresh_from_db()
        self.assertEqual(hours.instructor, self.instructor)

    def test_hours_move_between_own_instructors(self):
        hours = create_test_instructor_hours(self.instructor)
        second = create_test_instructor(self.user)

        response = self.client.patch(
            f'/instructor/{self.instructor.instructor_id}/hours/{hours.instructor_hours_id}/update',
            {'instructor': str(second.instructor_id)}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        hours.refresh_from_db()
        self.assertEqual(hours.instructor, second)

    def test_instructor_of_another_association_cannot_be_changed(self):
        response = self.client.patch(f'/instructor/{self.other_instructor.instructor_id}/update',
                                     {'first_name': 'X'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.delete(f'/instructor/{self.other_instructor.instructor_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.other_instructor.refresh_from_db()
        self.assertNotEqual(self.other_instructor.first_name, 'X')
