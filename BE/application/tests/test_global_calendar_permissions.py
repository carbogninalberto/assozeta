from uuid import uuid4
from django.utils import timezone
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_user, create_test_sport_association, create_test_course
from application.models import User, GlobalCalendarEvents, Reminders, Instructor, AttendanceRegistry


def event(month=9, **extra):
    return dict(event_id=str(uuid4()), title='Session', start=f'2026-{month:02}-20T10:00:00Z',
                end=f'2026-{month:02}-20T11:00:00Z', extendedProps={}, **extra)


class GlobalCalendarPermissionTests(BaseAPITestCase):
    url = '/calendar/events/update'

    def staff(self, *permissions, full=False):
        user = create_test_user(role=User.COLLABORATOR, connected_user=self.user,
            collaborator_role=User.FULL if full else User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=list(permissions))
        self.client.force_authenticate(user=user)
        return user

    def test_create_only_and_offscreen_preservation(self):
        old, new = event(8), event()
        calendar = GlobalCalendarEvents.objects.create(sport_association=self.sport_association, events=[old])
        self.staff('association.events.create')
        self.assertEqual(self.client.post(self.url, {'action':'create','events':[new]}, format='json').status_code, 200)
        calendar.refresh_from_db()
        self.assertEqual(len(calendar.events), 2)
        for action in ('update', 'delete'):
            self.assertEqual(self.client.post(self.url, {'action':action,'events':[new], 'event_id':new['event_id']}, format='json').status_code, 403)

    def test_update_cannot_create_or_delete_via_legacy_snapshot(self):
        old = event()
        calendar = GlobalCalendarEvents.objects.create(sport_association=self.sport_association, events=[old])
        self.staff('association.events.update')
        for events in ([], [old, event()]):
            self.assertEqual(self.client.post(self.url, {'events':events}, format='json').status_code, 403)
        edited = dict(old, title='Changed')
        self.assertEqual(self.client.post(self.url, {'action':'update','events':[edited]}, format='json').status_code, 200)
        calendar.refresh_from_db()
        self.assertEqual(calendar.events[0]['title'], 'Changed')

    def test_visibility_full_role_and_global_details(self):
        old = event()
        GlobalCalendarEvents.objects.create(sport_association=self.sport_association, events=[old])
        self.staff('association.calendar.read')
        self.assertEqual(self.client.get('/calendar/events').json()['data']['events'], [])
        self.staff(full=True)
        response = self.client.get('/calendar/events', {'event_id':old['event_id']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['events'][0]['event_id'], old['event_id'])
        self.staff('association.events.read')
        self.assertEqual(self.client.get('/calendar/events').status_code, 403)

    def test_deleted_reminders_are_scoped(self):
        old = event()
        GlobalCalendarEvents.objects.create(sport_association=self.sport_association, events=[old])
        other = create_test_sport_association()
        for association in (self.sport_association, other):
            Reminders.objects.create(event_id=old['event_id'], event_title='keep?', user=association.user,
                                     sport_association=association, send_at=timezone.now())
        self.staff('association.events.delete')
        response = self.client.post(self.url, {'action':'delete','event_id':old['event_id']}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(Reminders.objects.values_list('sport_association_id', flat=True)), [other.pk])

    def test_invalid_input_and_course_write_gate(self):
        self.staff('association.calendar.read')
        course = create_test_course(sport_association=self.sport_association)
        self.assertEqual(self.client.post(f'/course/{course.pk}/calendar/update', {'events':[]}, format='json').status_code, 403)
        self.staff(full=True)
        for events in (None, {}, [None], [event(start_bad='no') | {'start':'invalid'}], [event() | {'extendedProps':[]} ]):
            self.assertEqual(self.client.post(self.url, {'action':'create','events':events}, format='json').status_code, 400)
        self.assertEqual(self.client.get('/calendar/events', {'start':'no'}).status_code, 400)

    def test_instructor_sees_only_own_lessons(self):
        staff = self.staff('association.calendar.read')
        instructor = Instructor.objects.create(user=self.user, associated_user_id=staff.pk, first_name='Instructor', last_name='Test')
        course = create_test_course(sport_association=self.sport_association)
        own, someone_else = event(), event()
        own['extendedProps']['instructor'] = [{'instructor_id':str(instructor.pk)}]
        AttendanceRegistry.objects.create(course=course, events=[own,someone_else], status=AttendanceRegistry.PUBLISHED)
        response = self.client.get('/calendar/events')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([e['event_id'] for e in response.json()['data']['events']], [own['event_id']])
