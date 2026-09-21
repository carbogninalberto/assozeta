"""
Tests for instructor_lessons_hours endpoint: lesson hours computed
from the course calendar (AttendanceRegistry events).

Covers:
- association admin querying a single instructor and the whole list
- period filtering (start_date/end_date)
- instructor-tied user authorized only on their own hours
- foreign instructor / other association isolation
"""
import uuid

from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.models.user_models import Instructor
from application.models.attendee_models import AttendanceRegistry
from application.tests.base import BaseTransactionTestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_course,
)


def create_lessons_hours_instructor(user, **kwargs):
    defaults = {
        'user': user,
        'first_name': f'Instructor{uuid.uuid4().hex[:6]}',
        'last_name': f'Test{uuid.uuid4().hex[:6]}',
        'email': f'instructor_{uuid.uuid4().hex[:6]}@test.com',
        'tax_code': f'{uuid.uuid4().hex[:16].upper()}',
        'role': 'Istruttore',
        'draft': False,
        'is_volunteer': True,
        'default_hourly_billing': 20,
    }
    defaults.update(kwargs)
    return Instructor.objects.create(**defaults)


def make_lesson_event(instructor_id, start, end, event_id=None):
    return {
        'event_id': event_id or str(uuid.uuid4()),
        'title': 'Lezione',
        'start': start,
        'end': end,
        'allDay': False,
        'extendedProps': {
            'instructor': [{'instructor_id': str(instructor_id), 'label': 'Test', 'value': str(instructor_id)}],
        },
    }


class InstructorLessonsHoursTests(BaseTransactionTestCase):
    """Tests for instructor_lessons_hours: GET /instructor/lessons-hours and /instructor/<uid>/lessons-hours"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

        self.instructor = create_lessons_hours_instructor(self.user)
        self.course = create_test_course(sport_association=self.sport_association)

    def _create_registry(self, events):
        return AttendanceRegistry.objects.create(
            course=self.course,
            status=AttendanceRegistry.PUBLISHED,
            events=events,
        )

    def test_single_instructor_hours(self):
        self._create_registry([
            make_lesson_event(self.instructor.instructor_id, '2026-09-01T18:00:00Z', '2026-09-01T19:00:00Z'),
            make_lesson_event(self.instructor.instructor_id, '2026-09-02T18:00:00Z', '2026-09-02T20:00:00Z'),
        ])
        response = self.client.get(f'/instructor/{self.instructor.instructor_id}/lessons-hours')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['lessons_count'], 2)
        self.assertEqual(float(data['total_hours']), 3.0)
        self.assertEqual(len(data['courses']), 1)
        self.assertEqual(data['courses'][0]['lessons_count'], 2)
        self.assertEqual(float(data['courses'][0]['hours']), 3.0)

    def test_ignores_events_of_other_instructors(self):
        other = create_lessons_hours_instructor(self.user)
        self._create_registry([
            make_lesson_event(self.instructor.instructor_id, '2026-09-01T18:00:00Z', '2026-09-01T19:00:00Z'),
            make_lesson_event(other.instructor_id, '2026-09-01T18:00:00Z', '2026-09-01T19:00:00Z'),
        ])
        response = self.client.get(f'/instructor/{self.instructor.instructor_id}/lessons-hours')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['lessons_count'], 1)
        self.assertEqual(float(data['total_hours']), 1.0)

    def test_period_filter(self):
        self._create_registry([
            make_lesson_event(self.instructor.instructor_id, '2026-08-01T18:00:00Z', '2026-08-01T19:00:00Z'),
            make_lesson_event(self.instructor.instructor_id, '2026-09-01T18:00:00Z', '2026-09-01T19:00:00Z'),
        ])
        response = self.client.get(
            f'/instructor/{self.instructor.instructor_id}/lessons-hours',
            {'start_date': '15/08/2026', 'end_date': '15/09/2026'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['lessons_count'], 1)
        self.assertEqual(float(data['total_hours']), 1.0)

    def test_instructor_list_mode_for_association(self):
        other = create_lessons_hours_instructor(self.user)
        self._create_registry([
            make_lesson_event(self.instructor.instructor_id, '2026-09-01T18:00:00Z', '2026-09-01T19:00:00Z'),
            make_lesson_event(other.instructor_id, '2026-09-02T18:00:00Z', '2026-09-02T19:30:00Z'),
        ])
        response = self.client.get('/instructor/lessons-hours')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()['data']
        self.assertEqual(len(results), 2)
        by_id = {r['instructor_id']: r for r in results}
        self.assertEqual(float(by_id[str(self.instructor.instructor_id)]['total_hours']), 1.0)
        self.assertEqual(float(by_id[str(other.instructor_id)]['total_hours']), 1.5)

    def test_instructor_tied_user_sees_own_hours(self):
        athlete_user = create_test_user(role=User.ATHLETE)
        athlete_user.sport_association = self.sport_association
        athlete_user.save()
        self.instructor.associated_user_id = athlete_user.user_id
        self.instructor.save()

        self._create_registry([
            make_lesson_event(self.instructor.instructor_id, '2026-09-01T18:00:00Z', '2026-09-01T19:00:00Z'),
        ])

        client = APIClient()
        client.force_authenticate(user=athlete_user)
        response = client.get('/instructor/lessons-hours')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['instructor_id'], str(self.instructor.instructor_id))
        self.assertEqual(float(data['total_hours']), 1.0)

    def test_instructor_tied_user_cannot_query_others(self):
        other = create_lessons_hours_instructor(self.user)

        athlete_user = create_test_user(role=User.ATHLETE)
        athlete_user.sport_association = self.sport_association
        athlete_user.save()
        self.instructor.associated_user_id = athlete_user.user_id
        self.instructor.save()

        client = APIClient()
        client.force_authenticate(user=athlete_user)
        response = client.get(f'/instructor/{other.instructor_id}/lessons-hours')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_not_found_instructor(self):
        response = self.client.get(f'/instructor/{uuid.uuid4()}/lessons-hours')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/instructor/lessons-hours')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_registry(self):
        response = self.client.get(f'/instructor/{self.instructor.instructor_id}/lessons-hours')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()['data']
        self.assertEqual(data['lessons_count'], 0)
        self.assertEqual(float(data['total_hours']), 0.0)
        self.assertEqual(data['courses'], [])

    def test_naive_dates_and_last_day_are_included(self):
        self._create_registry([
            make_lesson_event(self.instructor.pk, '2026-09-30T18:00:00', '2026-09-30T20:00:00'),
            make_lesson_event(self.instructor.pk, '2026-10-01T00:00:00Z', '2026-10-01T01:00:00Z'),
        ])
        response = self.client.get(f'/instructor/{self.instructor.pk}/lessons-hours', {'start_date':'01/09/2026','end_date':'30/09/2026'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['total_hours'], 2)

    def test_drafts_malformed_events_and_negative_durations(self):
        event = make_lesson_event(self.instructor.pk, '2026-09-01T18:00:00Z', '2026-09-01T20:00:00Z')
        AttendanceRegistry.objects.create(course=self.course, status=AttendanceRegistry.DRAFT, events=[event])
        self._create_registry([None, 'invalid', {'start': None}, {**event,'extendedProps':[]},
            {**event,'extendedProps':{'instructor':['invalid']}}, {**event,'end':event['start']}, event])
        response = self.client.get(f'/instructor/{self.instructor.pk}/lessons-hours')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['total_hours'], 2)

    def test_invalid_and_reversed_dates(self):
        for params in ({'start_date':'invalid'}, {'end_date':'31/02/2026'}, {'start_date':'02/09/2026','end_date':'01/09/2026'}):
            self.assertEqual(self.client.get('/instructor/lessons-hours', params).status_code, 400)

    def test_instructor_collaborator_cannot_read_other_instructor(self):
        collaborator = create_test_user(role=User.COLLABORATOR, connected_user=self.user,
            collaborator_role=User.CUSTOM_COLLABORATOR_ROLE, collaborator_permissions=['association.instructor.read'])
        self.instructor.associated_user_id = collaborator.pk
        self.instructor.save()
        other = create_lessons_hours_instructor(self.user)
        self.client.force_authenticate(user=collaborator)
        self.assertEqual(self.client.get(f'/instructor/{other.pk}/lessons-hours').status_code, 403)
        self.assertEqual(self.client.get(f'/instructor/{other.pk}/info').status_code, 403)
        self.assertEqual(self.client.get('/instructor/lessons-hours').json()['data']['instructor_id'], str(self.instructor.pk))
        collaborator.collaborator_permissions = []
        collaborator.save()
        self.assertEqual(self.client.get('/instructor/lessons-hours').status_code, 403)

    def test_period_uses_rome_calendar_dates(self):
        self._create_registry([
            make_lesson_event(self.instructor.pk, '2026-09-01T00:30:00+02:00', '2026-09-01T01:30:00+02:00', 'included'),
            make_lesson_event(self.instructor.pk, '2026-10-01T00:30:00+02:00', '2026-10-01T02:30:00+02:00', 'excluded'),
        ])
        response = self.client.get(f'/instructor/{self.instructor.pk}/lessons-hours', {
            'start_date': '01/09/2026', 'end_date': '30/09/2026', 'include_lessons': 'true',
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['total_hours'], 1)
        self.assertEqual([lesson['event_id'] for lesson in data['courses'][0]['lessons']], ['included'])

    def test_period_respects_daylight_saving_transitions(self):
        for day, start, end, hours in [
            ('29/03/2026', '2026-03-29T00:00:00+01:00', '2026-03-30T00:00:00+02:00', 23),
            ('25/10/2026', '2026-10-25T00:00:00+02:00', '2026-10-26T00:00:00+01:00', 25),
        ]:
            with self.subTest(day=day):
                registry = self._create_registry([make_lesson_event(self.instructor.pk, start, end)])
                response = self.client.get(f'/instructor/{self.instructor.pk}/lessons-hours', {'start_date': day, 'end_date': day})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['data']['total_hours'], hours)
                registry.delete()

    def test_lesson_details_are_opt_in_scoped_sorted_and_clipped(self):
        other = create_lessons_hours_instructor(self.user)
        self._create_registry([
            make_lesson_event(self.instructor.pk, '2026-09-02T18:00:00Z', '2026-09-02T20:00:00Z', 'later'),
            make_lesson_event(other.pk, '2026-09-02T18:00:00Z', '2026-09-02T20:00:00Z', 'other-instructor'),
            make_lesson_event(self.instructor.pk, '2026-08-31T21:00:00Z', '2026-08-31T23:00:00Z', 'overlap'),
        ])
        other_user = create_test_user(role=User.ASSOCIATION)
        foreign_association = create_test_sport_association(user=other_user)
        foreign_course = create_test_course(sport_association=foreign_association)
        AttendanceRegistry.objects.create(course=foreign_course, status=AttendanceRegistry.PUBLISHED, events=[
            make_lesson_event(self.instructor.pk, '2026-09-02T18:00:00Z', '2026-09-02T20:00:00Z', 'foreign'),
        ])
        path = f'/instructor/{self.instructor.pk}/lessons-hours'
        params = {'start_date': '01/09/2026', 'end_date': '30/09/2026'}
        summary = self.client.get(path, params).json()['data']
        self.assertNotIn('lessons', summary['courses'][0])
        data = self.client.get(path, {**params, 'include_lessons': 'true'}).json()['data']
        self.assertEqual(data['total_hours'], 3)
        lessons = data['courses'][0]['lessons']
        self.assertEqual([lesson['event_id'] for lesson in lessons], ['overlap', 'later'])
        self.assertEqual(lessons[0]['hours'], 1)
        self.assertEqual(lessons[0]['start'], '2026-08-31T21:00:00+00:00')
        self.assertEqual(lessons[0]['title'], 'Lezione')
        self.assertEqual(lessons[1]['end'], '2026-09-02T20:00:00+00:00')
        listing = self.client.get('/instructor/lessons-hours', {'include_lessons': 'true'}).json()['data']
        self.assertTrue(all('lessons' not in course for item in listing for course in item['courses']))
        self.client.force_authenticate(user=other_user)
        self.assertEqual(self.client.get(path, {'include_lessons': 'true'}).status_code, 403)
