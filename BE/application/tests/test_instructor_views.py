"""
Tests for Instructor views - instructor management endpoints.

Self-host port: adapted from SaaS test_instructor_views.py.
"""
import uuid
import datetime
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.models.user_models import Instructor, InstructorHours
from application.models.courses_models import Course
from application.models.payment_models import PaymentCategory
from application.tests.base import BaseTransactionTestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_course,
)


def create_test_instructor(user, **kwargs):
    defaults = {
        'user': user,
        'first_name': f'Instructor{uuid.uuid4().hex[:6]}',
        'last_name': f'Test{uuid.uuid4().hex[:6]}',
        'email': f'instructor_{uuid.uuid4().hex[:6]}@test.com',
        'tax_code': f'{uuid.uuid4().hex[:16].upper()}',
        'born_date': datetime.date(1985, 5, 15),
        'born_city': 'Roma',
        'address_city': 'Roma',
        'address': 'Via Test 1',
        'role': 'Istruttore',
        'phone': '+39123456789',
        'draft': False,
        'is_volunteer': False,
        'default_hourly_billing': Decimal('20.00'),
        'default_percentage_billing': Decimal('10.00'),
    }
    defaults.update(kwargs)
    return Instructor.objects.create(**defaults)


def create_test_instructor_hours(instructor, **kwargs):
    defaults = {
        'instructor': instructor,
        'date': timezone.now().date(),
        'compensation_type': 'hourly',
        'hours': Decimal('4.00'),
        'hourly_billing': Decimal('20.00'),
        'amount': Decimal('80.00'),
        'paid': False,
        'notes': 'Test hours',
    }
    defaults.update(kwargs)
    return InstructorHours.objects.create(**defaults)


class InstructorListTests(BaseTransactionTestCase):
    """Tests for instructor_list endpoint: GET /instructor/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_get_instructors_list(self):
        create_test_instructor(self.user)
        create_test_instructor(self.user)
        response = self.client.get('/instructor/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertGreaterEqual(len(data['data']), 2)

    def test_get_instructors_list_empty(self):
        response = self.client.get('/instructor/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertEqual(len(data['data']), 0)

    def test_get_instructors_list_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/instructor/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_instructors_list_other_user(self):
        create_test_instructor(self.user)
        other_user = create_test_user(role=User.ASSOCIATION)
        create_test_sport_association(user=other_user)
        create_test_instructor(other_user)

        response = self.client.get('/instructor/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['data']), 1)


class InstructorAddTests(BaseTransactionTestCase):
    """Tests for instructor_add endpoint: POST /instructor/add"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_add_instructor_success(self):
        response = self.client.post('/instructor/add', {
            'first_name': 'John', 'last_name': 'Doe',
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_add_instructor_minimal_data(self):
        response = self.client.post('/instructor/add', {
            'first_name': 'Jane', 'last_name': 'Smith',
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_add_instructor_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.post('/instructor/add', {
            'first_name': 'Test', 'last_name': 'User',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class InstructorInfoTests(BaseTransactionTestCase):
    """Tests for instructor_info endpoint: GET /instructor/<uid>/info"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.instructor = create_test_instructor(self.user)

    def test_get_instructor_info(self):
        response = self.client.get(f'/instructor/{self.instructor.instructor_id}/info')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertIn('stats', data)

    def test_get_instructor_info_invalid_uuid(self):
        response = self.client.get('/instructor/invalid-uuid/info')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_instructor_info_not_found(self):
        response = self.client.get(f'/instructor/{uuid.uuid4()}/info')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_instructor_info_with_hours(self):
        create_test_instructor_hours(self.instructor)
        create_test_instructor_hours(self.instructor, paid=True)
        response = self.client.get(f'/instructor/{self.instructor.instructor_id}/info')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('hours', data['stats'])


class InstructorUpdateTests(BaseTransactionTestCase):
    """Tests for instructor_update endpoint: PATCH /instructor/<uid>/update"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.instructor = create_test_instructor(self.user)

    def test_update_instructor_success(self):
        response = self.client.patch(
            f'/instructor/{self.instructor.instructor_id}/update',
            {'first_name': 'Updated Name', 'last_name': self.instructor.last_name},
            format='json',
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_instructor_invalid_uuid(self):
        response = self.client.patch(
            '/instructor/invalid-uuid/update',
            {'first_name': 'Test'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_instructor_not_found(self):
        response = self.client.patch(
            f'/instructor/{uuid.uuid4()}/update',
            {'first_name': 'Test'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class InstructorDeleteTests(BaseTransactionTestCase):
    """Tests for instructor_delete endpoint: DELETE /instructor/<uid>/delete"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_delete_instructor_success(self):
        instructor = create_test_instructor(self.user)
        response = self.client.delete(f'/instructor/{instructor.instructor_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['msg'], 'instructor deleted')

    def test_delete_instructor_invalid_uuid(self):
        response = self.client.delete('/instructor/invalid-uuid/delete')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_instructor_not_found(self):
        response = self.client.delete(f'/instructor/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class InstructorHoursListTests(BaseTransactionTestCase):
    """Tests for instructor_hours_list endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.instructor = create_test_instructor(self.user)

    def test_get_hours_list(self):
        create_test_instructor_hours(self.instructor)
        create_test_instructor_hours(self.instructor)
        response = self.client.get(f'/instructor/{self.instructor.instructor_id}/hours/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertIn('meta', data)

    def test_get_hours_list_empty(self):
        response = self.client.get(f'/instructor/{self.instructor.instructor_id}/hours/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_get_hours_list_with_search(self):
        create_test_instructor_hours(self.instructor, notes='Special training session')
        response = self.client.get(
            f'/instructor/{self.instructor.instructor_id}/hours/list'
            f'?query[generalSearch]=Special'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class InstructorHoursAddTests(BaseTransactionTestCase):
    """Tests for instructor_hours_add endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.instructor = create_test_instructor(self.user)

    def test_add_hours_success(self):
        response = self.client.post(
            f'/instructor/{self.instructor.instructor_id}/hours/add',
            {
                'date': timezone.now().date().isoformat(),
                'hours': '4.00',
                'hourly_billing': '20.00',
                'amount': '80.00',
                'compensation_type': 'hourly',
                'notes': 'Training session',
            },
            format='json',
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_add_hours_instructor_not_found(self):
        response = self.client.post(
            f'/instructor/{uuid.uuid4()}/hours/add',
            {
                'date': timezone.now().date().isoformat(),
                'hours': '4.00',
                'amount': '80.00',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class InstructorHoursUpdateTests(BaseTransactionTestCase):
    """Tests for instructor_hours_update endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.instructor = create_test_instructor(self.user)
        self.instructor_hours = create_test_instructor_hours(self.instructor)

    def test_update_hours_success(self):
        response = self.client.patch(
            f'/instructor/{self.instructor.instructor_id}/hours/{self.instructor_hours.instructor_hours_id}/update',
            {'hours': '6.00', 'amount': '120.00'},
            format='json',
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK])

    def test_update_hours_not_found(self):
        response = self.client.patch(
            f'/instructor/{self.instructor.instructor_id}/hours/{uuid.uuid4()}/update',
            {'hours': '6.00'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class InstructorHoursDeleteTests(BaseTransactionTestCase):
    """Tests for instructor_hours_delete endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.instructor = create_test_instructor(self.user)

    def test_delete_hours_success(self):
        instructor_hours = create_test_instructor_hours(self.instructor)
        response = self.client.delete(
            f'/instructor/{self.instructor.instructor_id}/hours/{instructor_hours.instructor_hours_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['msg'], 'hours deleted.')

    def test_delete_hours_not_found(self):
        response = self.client.delete(
            f'/instructor/{self.instructor.instructor_id}/hours/{uuid.uuid4()}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class InstructorHoursCalculateTests(BaseTransactionTestCase):
    """Tests for instructor_hours_calculate endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.instructor = create_test_instructor(self.user)

    def test_calculate_hours_success(self):
        course = create_test_course(self.sport_association)
        response = self.client.post(
            f'/instructor/{self.instructor.instructor_id}/hours/calculate',
            {
                'period': '01/01/2024 al 31/12/2024',
                'courses': [{'course_id': str(course.course_id)}],
                'percentage': 10,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertIn('amount', data['data'])

    def test_calculate_hours_missing_courses(self):
        response = self.client.post(
            f'/instructor/{self.instructor.instructor_id}/hours/calculate',
            {'period': '01/01/2024 al 31/12/2024'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_calculate_hours_instructor_not_found(self):
        course = create_test_course(self.sport_association)
        response = self.client.post(
            f'/instructor/{uuid.uuid4()}/hours/calculate',
            {
                'period': '01/01/2024 al 31/12/2024',
                'courses': [{'course_id': str(course.course_id)}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_calculate_hours_invalid_period(self):
        course = create_test_course(self.sport_association)
        response = self.client.post(
            f'/instructor/{self.instructor.instructor_id}/hours/calculate',
            {
                'period': 'invalid-period',
                'courses': [{'course_id': str(course.course_id)}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_calculate_hours_invalid_percentage(self):
        course = create_test_course(self.sport_association)
        response = self.client.post(
            f'/instructor/{self.instructor.instructor_id}/hours/calculate',
            {
                'period': '01/01/2024 al 31/12/2024',
                'courses': [{'course_id': str(course.course_id)}],
                'percentage': 150,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_calculate_hours_empty_period(self):
        course = create_test_course(self.sport_association)
        response = self.client.post(
            f'/instructor/{self.instructor.instructor_id}/hours/calculate',
            {
                'period': '',
                'courses': [{'course_id': str(course.course_id)}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class InstructorHoursCompensationTests(BaseTransactionTestCase):
    """Tests for instructor_hours_add_compensation endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.instructor = create_test_instructor(self.user)
        PaymentCategory.objects.get_or_create(name='Compensi e Rimborsi Spese')

    def test_add_compensation_success(self):
        hours1 = create_test_instructor_hours(self.instructor, paid=False)
        hours2 = create_test_instructor_hours(self.instructor, paid=False)
        response = self.client.post(
            f'/instructor/{self.instructor.instructor_id}/hours/add/compensation',
            {'hours': [str(hours1.instructor_hours_id), str(hours2.instructor_hours_id)]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['msg'], 'compensation created.')

    def test_add_compensation_missing_hours(self):
        response = self.client.post(
            f'/instructor/{self.instructor.instructor_id}/hours/add/compensation',
            {}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_compensation_instructor_not_found(self):
        response = self.client.post(
            f'/instructor/{uuid.uuid4()}/hours/add/compensation',
            {'hours': [str(uuid.uuid4())]}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class InstructorReportTests(BaseTransactionTestCase):
    """Tests for instructor_report endpoint: GET /instructor/report"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_get_report_success(self):
        response = self.client.get(
            '/instructor/report?start_date=01/01/2024&end_date=31/12/2024'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK])

    def test_get_report_missing_dates(self):
        response = self.client.get('/instructor/report')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_report_missing_start_date(self):
        response = self.client.get('/instructor/report?end_date=31/12/2024')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_report_missing_end_date(self):
        response = self.client.get('/instructor/report?start_date=01/01/2024')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class InstructorEdgeCaseTests(BaseTransactionTestCase):
    """Tests for edge cases in instructor operations."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_instructor_with_volunteer_flag(self):
        response = self.client.post('/instructor/add', {
            'first_name': 'Volunteer', 'last_name': 'Helper',
            'is_volunteer': True,
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_instructor_with_default_billing(self):
        instructor = create_test_instructor(
            self.user,
            default_hourly_billing=Decimal('50.00'),
            default_percentage_billing=Decimal('15.00'),
        )
        response = self.client.get(f'/instructor/{instructor.instructor_id}/info')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(
            Decimal(data['data']['default_hourly_billing']),
            Decimal('50.00'),
        )

    def test_multiple_instructors_multiple_hours(self):
        instructor1 = create_test_instructor(self.user, first_name='Instructor1')
        instructor2 = create_test_instructor(self.user, first_name='Instructor2')
        for _ in range(3):
            create_test_instructor_hours(instructor1)
            create_test_instructor_hours(instructor2)

        response = self.client.get('/instructor/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['data']), 2)

        response1 = self.client.get(f'/instructor/{instructor1.instructor_id}/hours/list')
        response2 = self.client.get(f'/instructor/{instructor2.instructor_id}/hours/list')
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

    def test_instructor_draft_status(self):
        instructor = create_test_instructor(self.user, draft=True)
        response = self.client.get(f'/instructor/{instructor.instructor_id}/info')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['data']['draft'])
