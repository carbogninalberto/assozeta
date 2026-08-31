"""
Tests for Attendee views - calendar, attendance, and event management.

Self-host port: adapted from SaaS test_attendee_views.py.
"""
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone

from application.models import User, AttendanceRegistry, AttendanceDay, GlobalCalendarEvents, Reminders
from application.models.courses_models import Course, CourseSubscription
from application.models.subscriptions_models import Subscription
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.user_models import Associate, Instructor
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_billing_subscription,
    create_test_associate,
    create_test_subscription,
    create_test_course,
    create_test_payment,
)


def create_test_event(event_id=None, title='Test Event', start_offset_days=0, group_id=None):
    if event_id is None:
        event_id = str(uuid.uuid4())
    start_time = (datetime.now() + timedelta(days=start_offset_days)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    end_time = (datetime.now() + timedelta(days=start_offset_days, hours=1)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    event = {
        'event_id': event_id,
        'title': title,
        'start': start_time,
        'end': end_time,
        'allDay': False,
        'extendedProps': {
            'description': f'{title} description',
        }
    }
    if group_id:
        event['extendedProps']['groupId'] = group_id
    return event


def create_test_attendance_registry(course, **kwargs):
    defaults = {
        'course': course,
        'status': AttendanceRegistry.PUBLISHED,
        'events': [],
    }
    defaults.update(kwargs)
    return AttendanceRegistry.objects.create(**defaults)


def create_test_attendance_day(attendance_registry, **kwargs):
    defaults = {
        'attendance_registry': attendance_registry,
        'title': f'Day {uuid.uuid4().hex[:6]}',
        'date': timezone.now(),
        'attendees': [],
    }
    defaults.update(kwargs)
    return AttendanceDay.objects.create(**defaults)


def create_test_reminder(user, sport_association, event):
    return Reminders.objects.create(
        event_id=event['event_id'],
        event_title=event['title'],
        send_at=timezone.now(),
        user=user,
        sport_association=sport_association,
    )


def create_test_carnet(sport_association, subscription, course_subscription=None,
                       lessons_total=10, lessons_left=10, **kwargs):
    carnet = Carnet.objects.create(
        sport_association=sport_association,
        title=f'Test Carnet {uuid.uuid4().hex[:6]}',
        lessons_number=lessons_total,
        fee=Decimal('100.00'),
    )
    carnet_sub = CarnetSubscription.objects.create(
        carnet_id=carnet,
        subscription=subscription,
        meta={
            'lessons_left': lessons_left,
            'lessons_registry': [],
        },
        **kwargs
    )
    if course_subscription is not None:
        carnet_sub.course_subscription.add(course_subscription)
    return carnet, carnet_sub


class CalendarViewTests(BaseAPITestCase):
    """Tests for calendar endpoint: GET /course/<uid>/calendar"""

    def test_get_calendar_success(self):
        course = create_test_course(sport_association=self.sport_association)
        response = self.client.get(f'/course/{course.course_id}/calendar')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_get_calendar_with_registry(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Lesson 1')
        registry = create_test_attendance_registry(course, events=[event])

        response = self.client.get(f'/course/{course.course_id}/calendar')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['status'], AttendanceRegistry.PUBLISHED)
        self.assertEqual(len(response.data['data']['events']), 1)

    def test_get_calendar_course_not_found(self):
        response = self.client.get(f'/course/{uuid.uuid4()}/calendar')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_calendar_unauthenticated(self):
        self.client.force_authenticate(user=None)
        course = create_test_course(sport_association=self.sport_association)
        response = self.client.get(f'/course/{course.course_id}/calendar')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_calendar_returns_google_sync_status(self):
        self.user.integration_google_credentials = '{}'
        self.user.save()
        course = create_test_course(sport_association=self.sport_association)
        response = self.client.get(f'/course/{course.course_id}/calendar')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['data']['google_sync_enabled'])

    def test_get_calendar_invalid_uuid(self):
        response = self.client.get('/course/invalid-uuid/calendar')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CalendarUpdatePostTests(BaseAPITestCase):
    """Tests for calendar_update POST endpoint."""

    def test_create_draft_calendar(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event()
        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event],
            'status': AttendanceRegistry.DRAFT,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry = AttendanceRegistry.objects.get(course=course)
        self.assertEqual(registry.status, AttendanceRegistry.DRAFT)
        self.assertEqual(len(registry.events), 1)

    def test_create_published_calendar_creates_attendance_days(self):
        course = create_test_course(sport_association=self.sport_association)
        event1 = create_test_event(title='Lesson 1', start_offset_days=1)
        event2 = create_test_event(title='Lesson 2', start_offset_days=2)

        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event1, event2],
            'status': AttendanceRegistry.PUBLISHED,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        registry = AttendanceRegistry.objects.get(course=course)
        attendance_days = AttendanceDay.objects.filter(attendance_registry=registry)
        self.assertEqual(attendance_days.count(), 2)

    def test_update_draft_to_published(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event()
        registry = create_test_attendance_registry(
            course, status=AttendanceRegistry.DRAFT, events=[event],
        )

        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event],
            'status': AttendanceRegistry.PUBLISHED,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry.refresh_from_db()
        self.assertEqual(registry.status, AttendanceRegistry.PUBLISHED)

    def test_add_event_to_published_calendar(self):
        course = create_test_course(sport_association=self.sport_association)
        event1 = create_test_event(title='Lesson 1', start_offset_days=1)
        registry = create_test_attendance_registry(course, events=[event1])
        create_test_attendance_day(registry, title='Lesson 1', date=timezone.now() + timedelta(days=1))

        event2 = create_test_event(title='Lesson 2', start_offset_days=2)
        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event1, event2],
            'status': AttendanceRegistry.PUBLISHED,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry.refresh_from_db()
        self.assertEqual(len(registry.events), 2)

    def test_update_event_in_published_calendar(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Original Title', start_offset_days=1)
        registry = create_test_attendance_registry(course, events=[event])
        create_test_attendance_day(
            registry,
            title='Original Title',
            date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
        )

        event['title'] = 'Updated Title'
        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event],
            'status': AttendanceRegistry.PUBLISHED,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry.refresh_from_db()
        self.assertEqual(registry.events[0]['title'], 'Updated Title')

    def test_create_calendar_with_reminder(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Reminder Test')
        event['extendedProps']['reminder_enabled'] = True
        event['extendedProps']['reminder_amount'] = 30
        event['extendedProps']['reminder_unit'] = 'minutes'

        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event],
            'status': AttendanceRegistry.DRAFT,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        reminder = Reminders.objects.filter(event_id=event['event_id']).first()
        self.assertIsNotNone(reminder)
        self.assertEqual(reminder.event_title, 'Reminder Test')

    def test_update_calendar_disables_reminder(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Reminder Test')
        event['extendedProps']['reminder_enabled'] = True
        event['extendedProps']['reminder_amount'] = 30
        event['extendedProps']['reminder_unit'] = 'minutes'

        self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event],
            'status': AttendanceRegistry.DRAFT,
        }, format='json')
        self.assertTrue(Reminders.objects.filter(event_id=event['event_id']).exists())

        event['extendedProps']['reminder_enabled'] = False
        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event],
            'status': AttendanceRegistry.DRAFT,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Reminders.objects.filter(event_id=event['event_id']).exists())

    def test_create_calendar_missing_events(self):
        course = create_test_course(sport_association=self.sport_association)
        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'status': AttendanceRegistry.DRAFT,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_calendar_invalid_status(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event()
        response = self.client.post(f'/course/{course.course_id}/calendar/update', {
            'events': [event],
            'status': 'invalid_status',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_calendar_course_not_found(self):
        event = create_test_event()
        response = self.client.post(f'/course/{uuid.uuid4()}/calendar/update', {
            'events': [event],
            'status': AttendanceRegistry.DRAFT,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CalendarUpdateDeleteTests(BaseAPITestCase):
    """Tests for calendar_update DELETE endpoint."""

    def test_delete_single_event(self):
        course = create_test_course(sport_association=self.sport_association)
        event1 = create_test_event(title='Lesson 1', start_offset_days=1)
        event2 = create_test_event(title='Lesson 2', start_offset_days=2)
        registry = create_test_attendance_registry(course, events=[event1, event2])

        create_test_attendance_day(
            registry, title='Lesson 1',
            date=datetime.strptime(event1['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
            associated_event=event1['event_id'],
        )
        create_test_attendance_day(
            registry, title='Lesson 2',
            date=datetime.strptime(event2['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
            associated_event=event2['event_id'],
        )
        create_test_reminder(self.user, self.sport_association, event1)
        create_test_reminder(self.user, self.sport_association, event2)

        response = self.client.delete(f'/course/{course.course_id}/calendar/update', {
            'event_id': event1['event_id'],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry.refresh_from_db()
        self.assertEqual(len(registry.events), 1)
        self.assertEqual(registry.events[0]['title'], 'Lesson 2')
        self.assertSetEqual(
            set(AttendanceDay.objects.values_list('associated_event', flat=True)),
            {uuid.UUID(event2['event_id'])},
        )
        self.assertSetEqual(
            set(Reminders.objects.values_list('event_id', flat=True)),
            {uuid.UUID(event2['event_id'])},
        )

    def test_delete_all_events_deletes_registry(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Single Lesson')
        registry = create_test_attendance_registry(course, events=[event])
        create_test_attendance_day(
            registry, title='Single Lesson',
            date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
            associated_event=event['event_id'],
        )
        create_test_reminder(self.user, self.sport_association, event)

        original_registry_id = registry.attendance_registry_id
        response = self.client.delete(f'/course/{course.course_id}/calendar/update', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            AttendanceRegistry.objects.filter(attendance_registry_id=original_registry_id).exists()
        )
        new_registry = AttendanceRegistry.objects.filter(course=course).first()
        self.assertIsNotNone(new_registry)
        self.assertEqual(new_registry.events, [])
        self.assertEqual(new_registry.status, AttendanceRegistry.DRAFT)
        self.assertFalse(Reminders.objects.filter(event_id=event['event_id']).exists())

    def test_delete_events_by_group_id_all(self):
        course = create_test_course(sport_association=self.sport_association)
        group_id = str(uuid.uuid4())
        event1 = create_test_event(title='Recurring 1', start_offset_days=1, group_id=group_id)
        event2 = create_test_event(title='Recurring 2', start_offset_days=8, group_id=group_id)
        event3 = create_test_event(title='Other Event', start_offset_days=15)

        registry = create_test_attendance_registry(course, events=[event1, event2, event3])
        for event in [event1, event2, event3]:
            create_test_attendance_day(
                registry, title=event['title'],
                date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
                associated_event=event['event_id'],
            )
            create_test_reminder(self.user, self.sport_association, event)

        response = self.client.delete(f'/course/{course.course_id}/calendar/update', {
            'event_id': event1['event_id'],
            'groupId': group_id,
            'before': None,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry.refresh_from_db()
        self.assertEqual(len(registry.events), 1)
        self.assertEqual(registry.events[0]['title'], 'Other Event')
        self.assertSetEqual(
            set(AttendanceDay.objects.values_list('associated_event', flat=True)),
            {uuid.UUID(event3['event_id'])},
        )
        self.assertSetEqual(
            set(Reminders.objects.values_list('event_id', flat=True)),
            {uuid.UUID(event3['event_id'])},
        )

    def test_delete_events_by_group_id_before(self):
        course = create_test_course(sport_association=self.sport_association)
        group_id = str(uuid.uuid4())
        event1 = create_test_event(title='Recurring 1', start_offset_days=1, group_id=group_id)
        event2 = create_test_event(title='Recurring 2', start_offset_days=8, group_id=group_id)
        event3 = create_test_event(title='Recurring 3', start_offset_days=15, group_id=group_id)

        registry = create_test_attendance_registry(course, events=[event1, event2, event3])
        for event in [event1, event2, event3]:
            create_test_attendance_day(
                registry, title=event['title'],
                date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
                associated_event=event['event_id'],
            )
            create_test_reminder(self.user, self.sport_association, event)

        response = self.client.delete(f'/course/{course.course_id}/calendar/update', {
            'event_id': event2['event_id'],
            'groupId': group_id,
            'before': True,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry.refresh_from_db()
        self.assertEqual(len(registry.events), 1)
        self.assertEqual(registry.events[0]['title'], 'Recurring 3')
        self.assertSetEqual(
            set(AttendanceDay.objects.values_list('associated_event', flat=True)),
            {uuid.UUID(event3['event_id'])},
        )
        self.assertSetEqual(
            set(Reminders.objects.values_list('event_id', flat=True)),
            {uuid.UUID(event3['event_id'])},
        )

    def test_delete_events_by_group_id_after(self):
        course = create_test_course(sport_association=self.sport_association)
        group_id = str(uuid.uuid4())
        event1 = create_test_event(title='Recurring 1', start_offset_days=1, group_id=group_id)
        event2 = create_test_event(title='Recurring 2', start_offset_days=8, group_id=group_id)
        event3 = create_test_event(title='Recurring 3', start_offset_days=15, group_id=group_id)

        registry = create_test_attendance_registry(course, events=[event1, event2, event3])
        for event in [event1, event2, event3]:
            create_test_attendance_day(
                registry, title=event['title'],
                date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
                associated_event=event['event_id'],
            )
            create_test_reminder(self.user, self.sport_association, event)

        response = self.client.delete(f'/course/{course.course_id}/calendar/update', {
            'event_id': event2['event_id'],
            'groupId': group_id,
            'before': False,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        registry.refresh_from_db()
        self.assertEqual(len(registry.events), 1)
        self.assertEqual(registry.events[0]['title'], 'Recurring 1')
        self.assertSetEqual(
            set(AttendanceDay.objects.values_list('associated_event', flat=True)),
            {uuid.UUID(event1['event_id'])},
        )
        self.assertSetEqual(
            set(Reminders.objects.values_list('event_id', flat=True)),
            {uuid.UUID(event1['event_id'])},
        )

    def test_delete_event_rolls_back_related_deletions_when_registry_save_fails(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Atomic lesson')
        registry = create_test_attendance_registry(course, events=[event])
        create_test_attendance_day(
            registry,
            title=event['title'],
            date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
            associated_event=event['event_id'],
        )
        create_test_reminder(self.user, self.sport_association, event)

        with patch.object(AttendanceRegistry, 'save', side_effect=RuntimeError('save failed')):
            with self.assertRaises(RuntimeError):
                self.client.delete(f'/course/{course.course_id}/calendar/update', {
                    'event_id': event['event_id'],
                }, format='json')

        registry.refresh_from_db()
        self.assertEqual(registry.events, [event])
        self.assertTrue(AttendanceDay.objects.filter(associated_event=event['event_id']).exists())
        self.assertTrue(Reminders.objects.filter(event_id=event['event_id']).exists())

    def test_delete_draft_calendar_fails(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event()
        create_test_attendance_registry(
            course, status=AttendanceRegistry.DRAFT, events=[event],
        )
        response = self.client.delete(f'/course/{course.course_id}/calendar/update', format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_calendar_no_registry(self):
        course = create_test_course(sport_association=self.sport_association)
        response = self.client.delete(f'/course/{course.course_id}/calendar/update', format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AttendeesViewTests(BaseAPITestCase):
    """Tests for attendees endpoint: GET /course/<uid>/attendees"""

    def test_get_attendees_success(self):
        course = create_test_course(sport_association=self.sport_association)
        create_test_attendance_registry(course)
        response = self.client.get(f'/course/{course.course_id}/attendees')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_get_attendees_with_events(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Lesson 1', start_offset_days=0)
        registry = create_test_attendance_registry(course, events=[event])
        create_test_attendance_day(
            registry, title='Lesson 1',
            date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
            associated_event=event['event_id'],
        )
        response = self.client.get(f'/course/{course.course_id}/attendees')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['events']), 1)

    @patch('application.models.subscriptions_models.timezone.now')
    def test_get_attendees_exposes_missing_medical_countdown_to_instructor(self, now_mock):
        now_mock.return_value = datetime(2026, 8, 31, 10, 0, tzinfo=dt_timezone.utc)
        course = create_test_course(sport_association=self.sport_association)
        associate = create_test_associate(
            sport_association=self.sport_association,
            born_date=datetime(1990, 1, 1).date(),
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            creation_date=datetime(2026, 8, 21, 10, 0, tzinfo=dt_timezone.utc),
        )
        CourseSubscription.objects.create(course=course, subscription=subscription)
        event = create_test_event(title='Lesson with new member')
        registry = create_test_attendance_registry(course, events=[event])
        create_test_attendance_day(
            registry,
            title=event['title'],
            date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
            associated_event=event['event_id'],
        )

        response = self.client.get(f'/course/{course.course_id}/attendees')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        attendee = response.data['data']['events'][0]['extended_props']['potential_attendees'][0]
        label = attendee['subscription']['associate']['medical_label']
        self.assertIn('Mancante da 10 giorni', label)

    def test_get_attendees_no_registry(self):
        course = create_test_course(sport_association=self.sport_association)
        response = self.client.get(f'/course/{course.course_id}/attendees')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['events'], [])

    def test_get_attendees_course_not_found(self):
        response = self.client.get(f'/course/{uuid.uuid4()}/attendees')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AttendeesUpdateTests(BaseAPITestCase):
    """Tests for attendees_update endpoint with carnet business logic."""

    def setUp(self):
        super().setUp()
        self.course = create_test_course(sport_association=self.sport_association)
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )
        self.course_sub = CourseSubscription.objects.create(
            course=self.course, subscription=self.subscription,
        )
        event = create_test_event(title='Test Lesson')
        self.registry = create_test_attendance_registry(self.course, events=[event])
        self.attendance_day = create_test_attendance_day(
            self.registry,
            title='Test Lesson',
            date=datetime.strptime(event['start'], '%Y-%m-%dT%H:%M:%S.000Z').replace(tzinfo=dt_timezone.utc),
        )

    def test_add_attendee_without_carnet(self):
        attendee_data = {
            'attendees': [{
                'course_subscription_id': str(self.course_sub.course_subscription_id),
                'associate_name': self.associate.get_full_name(),
            }]
        }
        response = self.client.post(
            f'/course/{self.course.course_id}/attendees/{self.attendance_day.attendance_day_id}/update',
            attendee_data, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.attendance_day.refresh_from_db()
        self.assertEqual(len(self.attendance_day.attendees), 1)

    def test_add_attendee_with_carnet_deducts_lesson(self):
        carnet, carnet_sub = create_test_carnet(
            self.sport_association, self.subscription, self.course_sub,
            lessons_total=10, lessons_left=5,
        )
        attendee_data = {
            'attendees': [{
                'course_subscription_id': str(self.course_sub.course_subscription_id),
                'associate_name': self.associate.get_full_name(),
            }]
        }
        response = self.client.post(
            f'/course/{self.course.course_id}/attendees/{self.attendance_day.attendance_day_id}/update',
            attendee_data, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        carnet_sub.refresh_from_db()
        self.assertEqual(carnet_sub.meta['lessons_left'], 4)
        self.assertEqual(len(carnet_sub.meta['lessons_registry']), 1)

    def test_remove_attendee_with_carnet_restores_lesson(self):
        carnet, carnet_sub = create_test_carnet(
            self.sport_association, self.subscription, self.course_sub,
            lessons_total=10, lessons_left=4,
        )
        carnet_sub.meta['lessons_registry'] = [{
            'date': str(self.attendance_day.date),
            'attendance_day_id': str(self.attendance_day.attendance_day_id),
            'course': {'id': str(self.course.course_id), 'title': self.course.title},
            'title': 'Test Lesson',
        }]
        carnet_sub.save()

        self.attendance_day.attendees = [{
            'course_subscription_id': str(self.course_sub.course_subscription_id),
            'associate_name': self.associate.get_full_name(),
        }]
        self.attendance_day.save()

        response = self.client.post(
            f'/course/{self.course.course_id}/attendees/{self.attendance_day.attendance_day_id}/update',
            {'attendees': []}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        carnet_sub.refresh_from_db()
        self.assertEqual(carnet_sub.meta['lessons_left'], 5)
        self.assertEqual(len(carnet_sub.meta['lessons_registry']), 0)

    def test_add_attendee_carnet_empty_fails(self):
        carnet, carnet_sub = create_test_carnet(
            self.sport_association, self.subscription, self.course_sub,
            lessons_total=10, lessons_left=0,
        )
        attendee_data = {
            'attendees': [{
                'course_subscription_id': str(self.course_sub.course_subscription_id),
                'associate_name': self.associate.get_full_name(),
            }]
        }
        response = self.client.post(
            f'/course/{self.course.course_id}/attendees/{self.attendance_day.attendance_day_id}/update',
            attendee_data, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_412_PRECONDITION_FAILED)

    def test_add_attendee_carnet_unpaid_fails(self):
        payment = create_test_payment(
            sport_association=self.sport_association,
            associate=self.associate,
            paid=False,
        )
        carnet, carnet_sub = create_test_carnet(
            self.sport_association, self.subscription, self.course_sub,
            lessons_total=10, lessons_left=5,
            payment=payment,
        )
        attendee_data = {
            'attendees': [{
                'course_subscription_id': str(self.course_sub.course_subscription_id),
                'associate_name': self.associate.get_full_name(),
            }]
        }
        response = self.client.post(
            f'/course/{self.course.course_id}/attendees/{self.attendance_day.attendance_day_id}/update',
            attendee_data, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_412_PRECONDITION_FAILED)

    def test_update_attendees_course_not_found(self):
        response = self.client.post(
            f'/course/{uuid.uuid4()}/attendees/{uuid.uuid4()}/update',
            {'attendees': []}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_attendees_missing_data(self):
        response = self.client.post(
            f'/course/{self.course.course_id}/attendees/{self.attendance_day.attendance_day_id}/update',
            {}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_attendees_registry_not_found(self):
        course_no_registry = create_test_course(sport_association=self.sport_association)
        response = self.client.post(
            f'/course/{course_no_registry.course_id}/attendees/{uuid.uuid4()}/update',
            {'attendees': []}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SubscriptionCalendarTests(BaseAPITestCase):
    """Tests for subscription_calendar endpoint."""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )

    def test_get_subscription_calendar(self):
        response = self.client.get(
            f'/subscription/{self.subscription.subscription_id}/calendar'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('events', response.data['data'])

    def test_get_subscription_calendar_with_course_events(self):
        course = create_test_course(sport_association=self.sport_association, title='Yoga')
        CourseSubscription.objects.create(course=course, subscription=self.subscription)
        event = create_test_event(title='Yoga Session')
        create_test_attendance_registry(course, events=[event])

        response = self.client.get(
            f'/subscription/{self.subscription.subscription_id}/calendar'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['events']), 1)

    def test_get_subscription_calendar_as_athlete(self):
        athlete = create_test_user(role=User.ATHLETE)
        associate = create_test_associate(
            sport_association=self.sport_association, user=athlete,
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate, user=athlete,
        )

        self.client.force_authenticate(user=athlete)
        response = self.client.get(
            f'/subscription/{subscription.subscription_id}/calendar'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SubscriptionAttendanceTests(BaseAPITestCase):
    """Tests for subscription_attendance endpoint."""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )

    def test_get_subscription_attendance(self):
        response = self.client.get(
            f'/subscription/{self.subscription.subscription_id}/attendance'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('attendance_days', response.data['data'])
        self.assertIn('stats', response.data['data'])

    def test_get_subscription_attendance_with_records(self):
        course = create_test_course(sport_association=self.sport_association)
        course_sub = CourseSubscription.objects.create(
            course=course, subscription=self.subscription,
        )

        event = create_test_event(title='Lesson')
        registry = create_test_attendance_registry(course, events=[event])
        create_test_attendance_day(
            registry, title='Lesson',
            date=timezone.now() - timedelta(days=5),
            attendees=[{'course_subscription_id': str(course_sub.course_subscription_id)}],
        )

        response = self.client.get(
            f'/subscription/{self.subscription.subscription_id}/attendance'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['stats']['total_attendance'], 1)
        self.assertEqual(response.data['data']['stats']['total_attendance_last_30_days'], 1)

    def test_get_subscription_attendance_filter_by_course(self):
        course = create_test_course(sport_association=self.sport_association)
        CourseSubscription.objects.create(
            course=course, subscription=self.subscription,
        )

        response = self.client.get(
            f'/subscription/{self.subscription.subscription_id}/attendance?course={course.course_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class FullEventsCalendarTests(BaseAPITestCase):
    """Tests for full_events_calendar endpoint: GET /calendar/events"""

    def test_get_full_events_calendar(self):
        response = self.client.get('/calendar/events')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('events', response.data['data'])

    def test_get_full_events_calendar_with_events(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Yoga Class')
        create_test_attendance_registry(course, events=[event])
        response = self.client.get('/calendar/events')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['events']), 1)

    def test_get_full_events_calendar_with_dates(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Future Event', start_offset_days=5)
        create_test_attendance_registry(course, events=[event])

        start = (datetime.now() + timedelta(days=1)).isoformat()
        end = (datetime.now() + timedelta(days=30)).isoformat()
        response = self.client.get(f'/calendar/events?start={start}&end={end}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_full_events_calendar_by_event_id(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Specific Event')
        create_test_attendance_registry(course, events=[event])

        response = self.client.get(f'/calendar/events?event_id={event["event_id"]}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if response.data['data']['events']:
            self.assertEqual(
                response.data['data']['events'][0]['title'], 'Specific Event'
            )

    def test_get_full_events_calendar_includes_global_events(self):
        global_event = create_test_event(title='Holiday', start_offset_days=5)
        GlobalCalendarEvents.objects.create(
            sport_association=self.sport_association,
            events=[global_event],
        )

        start = (datetime.now() + timedelta(days=1)).isoformat()
        end = (datetime.now() + timedelta(days=30)).isoformat()
        response = self.client.get(f'/calendar/events?start={start}&end={end}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event_titles = [e['title'] for e in response.data['data']['events']]
        self.assertIn('Holiday', event_titles)

    def test_get_full_events_calendar_as_athlete_forbidden(self):
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.get('/calendar/events')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class FullEventsCalendarUpdateTests(BaseAPITestCase):
    """Tests for full_events_calendar_update endpoint: POST /calendar/events/update"""

    def test_update_full_events_calendar(self):
        event = create_test_event(title='Global Event')
        response = self.client.post('/calendar/events/update', {
            'events': [event],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        global_events = GlobalCalendarEvents.objects.get(
            sport_association=self.sport_association
        )
        self.assertEqual(len(global_events.events), 1)

    def test_update_full_events_calendar_creates_reminder(self):
        event = create_test_event(title='Event With Reminder')
        event['extendedProps']['reminder_enabled'] = True
        event['extendedProps']['reminder_amount'] = 1
        event['extendedProps']['reminder_unit'] = 'hours'

        response = self.client.post('/calendar/events/update', {
            'events': [event],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reminder = Reminders.objects.filter(event_id=event['event_id']).first()
        self.assertIsNotNone(reminder)

    def test_update_full_events_calendar_deletes_reminder(self):
        event = create_test_event(title='Event With Reminder')
        event['extendedProps']['reminder_enabled'] = True
        event['extendedProps']['reminder_amount'] = 1
        event['extendedProps']['reminder_unit'] = 'hours'

        self.client.post('/calendar/events/update', {'events': [event]}, format='json')
        self.assertTrue(Reminders.objects.filter(event_id=event['event_id']).exists())

        event['extendedProps']['reminder_enabled'] = False
        response = self.client.post('/calendar/events/update', {
            'events': [event],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Reminders.objects.filter(event_id=event['event_id']).exists())

    def test_update_full_events_calendar_missing_events(self):
        response = self.client.post('/calendar/events/update', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CalendarExportTests(BaseAPITestCase):
    """Tests for full_events_calendar_export endpoint: GET /calendar/events/export"""

    def test_export_calendar_empty(self):
        response = self.client.get('/calendar/events/export')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/calendar')

    def test_export_calendar_with_events(self):
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Exportable Event')
        event['start'] = '2026-08-27T13:00:00.000Z'
        event['end'] = '2026-08-27T14:00:00.000Z'
        create_test_attendance_registry(course, events=[event])

        response = self.client.get('/calendar/events/export')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/calendar')
        self.assertIn(b'BEGIN:VCALENDAR', response.content)
        self.assertIn(b'Exportable Event', response.content)
        self.assertIn(b'DTSTART:20260827T130000Z', response.content)
        self.assertIn(b'DTEND:20260827T140000Z', response.content)

    def test_export_calendar_filter_by_course(self):
        course1 = create_test_course(
            sport_association=self.sport_association, title='Course 1',
        )
        course2 = create_test_course(
            sport_association=self.sport_association, title='Course 2',
        )

        event1 = create_test_event(title='Event 1')
        event2 = create_test_event(title='Event 2')
        create_test_attendance_registry(course1, events=[event1])
        create_test_attendance_registry(course2, events=[event2])

        response = self.client.get(
            f'/calendar/events/export?course_id={course1.course_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b'Event 1', response.content)
        self.assertNotIn(b'Event 2', response.content)

    def test_export_calendar_as_athlete_forbidden(self):
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.get('/calendar/events/export')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_export_calendar_with_instructor(self):
        instructor = Instructor.objects.create(
            user=self.user,
            first_name='John',
            last_name='Trainer',
        )
        course = create_test_course(sport_association=self.sport_association)
        event = create_test_event(title='Instructor Event')
        event['extendedProps']['instructor'] = {
            'instructor_id': str(instructor.instructor_id),
            'label': 'John Trainer',
        }
        create_test_attendance_registry(course, events=[event])

        response = self.client.get('/calendar/events/export')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b'John Trainer', response.content)
