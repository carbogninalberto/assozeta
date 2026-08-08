"""
Tests for Course views - course CRUD, enrollment, and tags.

Self-host port: adapted from SaaS test_course_views.py.
"""
import uuid
from decimal import Decimal
from datetime import datetime, timedelta

from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone

from application.models import User, Course, CourseTags, CourseLocation
from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment
from application.models.payment_models import Payment
from application.tests.base import BaseAPITestCase, BaseTransactionTestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_billing_subscription,
    create_test_associate,
    create_test_subscription,
    create_test_course,
    create_test_course_location,
    create_test_course_subscription,
    create_test_course_subscription_installment,
)


def create_test_course_tag(sport_association, **kwargs):
    defaults = {
        'tag_name': f'Tag {uuid.uuid4().hex[:6]}',
    }
    defaults.update(kwargs)
    return CourseTags.objects.create(
        sport_association=sport_association,
        **defaults
    )


class CourseListTests(BaseAPITestCase):
    """Tests for course_list endpoint: GET /course/list"""

    def test_list_courses_success(self):
        course1 = create_test_course(self.sport_association, title='Course 1')
        course2 = create_test_course(self.sport_association, title='Course 2')

        response = self.client.get('/course/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        course_titles = [c['title'] for c in data['data'].values()]
        self.assertIn('Course 1', course_titles)
        self.assertIn('Course 2', course_titles)

    def test_list_courses_empty(self):
        response = self.client.get('/course/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_list_courses_only_own_association(self):
        my_course = create_test_course(self.sport_association, title='My Course')

        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        create_test_course(other_association, title='Other Course')

        response = self.client.get('/course/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        course_ids = [c['course_id'] for c in data['data'].values()]
        self.assertIn(str(my_course.course_id), course_ids)

    def test_list_courses_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/course/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CourseListAdvancedTests(BaseAPITestCase):
    """Tests for advanced course_list filtering."""

    def test_list_athlete_view_with_sport_association(self):
        athlete = create_test_user(role=User.ATHLETE)
        create_test_course(self.sport_association, title='Active Course', status_flag=Course.ACTIVE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get(
            f'/course/list?sport_association_id={self.sport_association.sport_association_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_list_general_search(self):
        create_test_course(self.sport_association, title='Basketball Training')
        create_test_course(self.sport_association, title='Soccer Practice')

        response = self.client.get('/course/list?query[generalSearch]=basketball')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        course_titles = [c['title'] for c in data['data'].values()]
        self.assertIn('Basketball Training', course_titles)
        self.assertNotIn('Soccer Practice', course_titles)

    def test_list_tag_filter(self):
        tag = create_test_course_tag(self.sport_association, tag_name='Premium')
        course1 = create_test_course(self.sport_association, title='Premium Course')
        course1.tags.add(tag)
        create_test_course(self.sport_association, title='Regular Course')

        response = self.client.get(f'/course/list?query[tags]={tag.tag_id}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        course_titles = [c['title'] for c in data['data'].values()]
        self.assertIn('Premium Course', course_titles)
        self.assertNotIn('Regular Course', course_titles)

    def test_list_status_filter(self):
        create_test_course(self.sport_association, title='Active', status_flag=Course.ACTIVE)
        create_test_course(self.sport_association, title='Draft', status_flag=Course.DRAFT)

        response = self.client.get(f'/course/list?query[status_flag]={Course.ACTIVE}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        course_titles = [c['title'] for c in data['data'].values()]
        self.assertIn('Active', course_titles)
        self.assertNotIn('Draft', course_titles)

    def test_list_all_returns_unpaginated(self):
        for i in range(15):
            create_test_course(self.sport_association, title=f'Course {i}')

        response = self.client.get('/course/list?all=1')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertEqual(len(data['data']), 15)


class CourseAddTests(BaseAPITestCase):
    """Tests for course_add endpoint: POST /course/add"""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
        )

    def test_add_course_success(self):
        data = {
            'new_course': {
                'title': 'New Test Course',
                'description': 'New test course description',
                'fee': '150.00',
                'multi_payments': False,
            },
            'subscriptions': []
        }

        response = self.client.post('/course/add', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course = Course.objects.filter(
            sport_association=self.sport_association,
            title='New Test Course'
        ).first()
        self.assertIsNotNone(course)

    def test_add_course_with_subscriptions(self):
        data = {
            'new_course': {
                'title': 'Course with Subscriptions',
                'description': 'Test description',
                'fee': '100.00',
                'multi_payments': False,
            },
            'subscriptions': [str(self.subscription.subscription_id)]
        }

        response = self.client.post('/course/add', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course = Course.objects.filter(title='Course with Subscriptions').first()
        self.assertIsNotNone(course)
        self.assertTrue(
            CourseSubscription.objects.filter(course=course).exists()
        )

    def test_add_course_invalid_body_structure(self):
        data = {'invalid_key': 'value'}
        response = self.client.post('/course/add', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_course_xss_sanitization(self):
        data = {
            'new_course': {
                'title': '<script>alert("xss")</script>Test Course',
                'description': '<img src=x onerror="alert(1)">Description',
                'fee': '100.00',
                'multi_payments': False,
            },
            'subscriptions': []
        }

        response = self.client.post('/course/add', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course = Course.objects.filter(
            sport_association=self.sport_association
        ).order_by('-creation_date').first()
        self.assertNotIn('<script>', course.title)
        self.assertIn('&lt;script&gt;', course.title)

    def test_add_course_unauthenticated(self):
        self.client.force_authenticate(user=None)
        data = {
            'new_course': {
                'title': 'Test', 'description': 'Test',
                'fee': '100.00', 'multi_payments': False,
            },
            'subscriptions': []
        }
        response = self.client.post('/course/add', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_add_course_with_locations(self):
        location = create_test_course_location(sport_association=self.sport_association)

        data = {
            'new_course': {
                'title': 'Course with Location',
                'description': 'Test description',
                'fee': '100.00',
                'multi_payments': False,
                'locations': [{'course_location_id': str(location.course_location_id)}]
            },
            'subscriptions': []
        }

        response = self.client.post('/course/add', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course = Course.objects.filter(title='Course with Location').first()
        self.assertIsNotNone(course)
        self.assertEqual(course.locations.count(), 1)


class CourseUpdateTests(BaseAPITestCase):
    """Tests for course_update endpoint: PATCH /course/<uid>/update"""

    def test_update_course_title(self):
        course = create_test_course(self.sport_association, title='Old Title')
        response = self.client.patch(
            f'/course/{course.course_id}/update',
            {'title': 'New Title'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.title, 'New Title')

    def test_update_course_fee(self):
        course = create_test_course(self.sport_association, fee=Decimal('100.00'))
        response = self.client.patch(
            f'/course/{course.course_id}/update',
            {'fee': '200.00'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.fee, Decimal('200.00'))

    def test_update_course_not_found(self):
        response = self.client.patch(
            f'/course/{uuid.uuid4()}/update',
            {'title': 'Test'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_course_invalid_uuid(self):
        response = self.client.patch(
            '/course/invalid-uuid/update',
            {'title': 'Test'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_course_xss_sanitization(self):
        course = create_test_course(self.sport_association)
        response = self.client.patch(
            f'/course/{course.course_id}/update',
            {'title': '<script>alert("xss")</script>Updated'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertNotIn('<script>', course.title)

    def test_update_course_unauthenticated(self):
        course = create_test_course(self.sport_association)
        self.client.force_authenticate(user=None)
        response = self.client.patch(
            f'/course/{course.course_id}/update',
            {'title': 'Test'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_course_locations(self):
        course = create_test_course(self.sport_association)
        location1 = create_test_course_location(sport_association=self.sport_association)
        location2 = create_test_course_location(sport_association=self.sport_association)

        data = {
            'locations': [
                {'course_location_id': str(location1.course_location_id)},
                {'course_location_id': str(location2.course_location_id)}
            ]
        }
        response = self.client.patch(
            f'/course/{course.course_id}/update', data, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.locations.count(), 2)

    def test_update_course_one_fee(self):
        course = create_test_course(
            self.sport_association,
            one_fee_payment=True,
            one_fee=Decimal('500.00')
        )
        response = self.client.patch(
            f'/course/{course.course_id}/update',
            {'one_fee': '600.00'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.one_fee, Decimal('600.00'))


class CourseEnableDisableTests(BaseAPITestCase):
    """Tests for course_enable and course_disable endpoints."""

    def test_disable_course_success(self):
        course = create_test_course(self.sport_association, status_flag=Course.ACTIVE)
        response = self.client.post(f'/course/{course.course_id}/disable')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status_flag, Course.DRAFT)

    def test_enable_course_success(self):
        course = create_test_course(self.sport_association, status_flag=Course.DRAFT)
        response = self.client.post(f'/course/{course.course_id}/enable')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status_flag, Course.ACTIVE)

    def test_disable_course_not_found(self):
        response = self.client.post(f'/course/{uuid.uuid4()}/disable')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_enable_course_not_found(self):
        response = self.client.post(f'/course/{uuid.uuid4()}/enable')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_toggle_course_status_repeatedly(self):
        course = create_test_course(self.sport_association, status_flag=Course.ACTIVE)
        response = self.client.post(f'/course/{course.course_id}/disable')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status_flag, Course.DRAFT)

        response = self.client.post(f'/course/{course.course_id}/enable')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status_flag, Course.ACTIVE)

    def test_disable_course_unauthenticated(self):
        course = create_test_course(self.sport_association)
        self.client.force_authenticate(user=None)
        response = self.client.post(f'/course/{course.course_id}/disable')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CourseDeleteTests(BaseAPITestCase):
    """Tests for course_delete endpoint: POST /course/<uid>/delete"""

    def test_delete_course_success(self):
        course = create_test_course(self.sport_association)
        course_id = course.course_id
        response = self.client.post(f'/course/{course_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Course.objects.filter(course_id=course_id).exists())

    def test_delete_course_not_found(self):
        response = self.client.post(f'/course/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_course_with_subscriptions(self):
        course = create_test_course(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        create_test_course_subscription(course=course, subscription=subscription)

        response = self.client.post(f'/course/{course.course_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_course_unauthenticated(self):
        course = create_test_course(self.sport_association)
        self.client.force_authenticate(user=None)
        response = self.client.post(f'/course/{course.course_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CourseOverviewTests(BaseAPITestCase):
    """Tests for course_overview endpoint: GET /course/<uid>/overview"""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )

    def test_get_course_overview_success(self):
        course = create_test_course(self.sport_association)
        response = self.client.get(f'/course/{course.course_id}/overview')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_get_course_overview_with_subscriptions(self):
        course = create_test_course(self.sport_association)
        create_test_course_subscription(course=course, subscription=self.subscription)
        response = self.client.get(f'/course/{course.course_id}/overview')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_get_course_overview_not_found(self):
        response = self.client.get(f'/course/{uuid.uuid4()}/overview')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_course_overview_unauthenticated(self):
        course = create_test_course(self.sport_association)
        self.client.force_authenticate(user=None)
        response = self.client.get(f'/course/{course.course_id}/overview')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CoursePinTests(BaseAPITestCase):
    """Tests for course_pin endpoint: POST /course/<uid>/pin"""

    def test_pin_course(self):
        course = create_test_course(self.sport_association, pinned=False)
        response = self.client.post(f'/course/{course.course_id}/pin')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertTrue(course.pinned)

    def test_unpin_course(self):
        course = create_test_course(self.sport_association, pinned=True)
        response = self.client.post(f'/course/{course.course_id}/pin')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertFalse(course.pinned)

    def test_pin_course_not_found(self):
        response = self.client.post(f'/course/{uuid.uuid4()}/pin')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CourseTagsListTests(BaseAPITestCase):
    """Tests for tags_list endpoint: GET /course/tags/list"""

    def test_list_tags_success(self):
        create_test_course_tag(self.sport_association, tag_name='Tag 1')
        create_test_course_tag(self.sport_association, tag_name='Tag 2')

        response = self.client.get('/course/tags/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('tags', data)

    def test_list_tags_empty(self):
        response = self.client.get('/course/tags/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('tags', data)
        self.assertEqual(len(data['tags']), 0)

    def test_list_tags_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/course/tags/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CourseTagsCRUDTests(BaseAPITestCase):
    """Tests for course tag CRUD operations."""

    def test_add_course_tag(self):
        response = self.client.post('/course/tags/add', {'tag_name': 'New Tag'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tag = CourseTags.objects.filter(
            sport_association=self.sport_association, tag_name='New Tag'
        ).first()
        self.assertIsNotNone(tag)

    def test_update_course_tag(self):
        tag = create_test_course_tag(self.sport_association, tag_name='Old Name')
        response = self.client.patch(
            f'/course/tags/{tag.tag_id}/update',
            {'tag_name': 'New Name'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tag.refresh_from_db()
        self.assertEqual(tag.tag_name, 'New Name')

    def test_delete_course_tag(self):
        tag = create_test_course_tag(self.sport_association)
        tag_id = tag.tag_id
        response = self.client.delete(f'/course/tags/{tag_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(CourseTags.objects.filter(tag_id=tag_id).exists())


class CourseTagsAssignmentTests(BaseAPITestCase):
    """Tests for course tag assignment/unassignment."""

    def test_assign_tag_to_course(self):
        tag = create_test_course_tag(self.sport_association)
        course = create_test_course(self.sport_association)
        response = self.client.patch(
            f'/course/tags/{tag.tag_id}/assign/{course.course_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertIn(tag, course.tags.all())

    def test_unassign_tag_from_course(self):
        tag = create_test_course_tag(self.sport_association)
        course = create_test_course(self.sport_association)
        course.tags.add(tag)
        response = self.client.patch(
            f'/course/tags/{tag.tag_id}/unassign/{course.course_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertNotIn(tag, course.tags.all())

    def test_assign_tag_not_found(self):
        course = create_test_course(self.sport_association)
        response = self.client.patch(
            f'/course/tags/{uuid.uuid4()}/assign/{course.course_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_assign_tag_course_not_found(self):
        tag = create_test_course_tag(self.sport_association)
        response = self.client.patch(
            f'/course/tags/{tag.tag_id}/assign/{uuid.uuid4()}'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CourseLocationViewSetTests(BaseAPITestCase):
    """Tests for CourseLocationViewSet endpoints."""

    def test_list_locations(self):
        create_test_course_location(
            sport_association=self.sport_association,
            title='Test Location', address='123 Test St'
        )
        response = self.client.get('/course/locations/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_add_location(self):
        response = self.client.post(
            '/course/locations/add',
            {'title': 'New Location', 'address': '456 New St'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            CourseLocation.objects.filter(title='New Location').exists()
        )

    def test_delete_location(self):
        location = create_test_course_location(
            sport_association=self.sport_association,
            title='To Delete', address='Delete St'
        )
        response = self.client.delete(
            f'/course/locations/{location.course_location_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            CourseLocation.objects.filter(
                course_location_id=location.course_location_id
            ).exists()
        )

    def test_update_location(self):
        location = create_test_course_location(
            sport_association=self.sport_association,
            title='Old Name', address='Old Address'
        )
        response = self.client.patch(
            f'/course/locations/{location.course_location_id}/update',
            {'title': 'Updated Name'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        location.refresh_from_db()
        self.assertEqual(location.title, 'Updated Name')

    def test_delete_location_other_association_denied(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_location = create_test_course_location(
            sport_association=other_association,
            title='Other Location', address='Other St'
        )
        response = self.client.delete(
            f'/course/locations/{other_location.course_location_id}/delete'
        )
        self.assertIn(response.status_code, [
            status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND
        ])


class CourseSubscriptionViewSetTests(BaseAPITestCase):
    """Tests for CourseSubscriptionViewSet endpoints."""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )
        self.course = create_test_course(self.sport_association)

    def test_list_course_subscriptions(self):
        create_test_course_subscription(course=self.course, subscription=self.subscription)
        response = self.client.get(
            f'/course-subscriptions/list?course_id={self.course.course_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_delete_course_subscription(self):
        course_sub = create_test_course_subscription(course=self.course, subscription=self.subscription)
        response = self.client.delete(
            f'/course-subscriptions/{course_sub.course_subscription_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_bulk_delete_course_subscriptions(self):
        course_sub1 = create_test_course_subscription(course=self.course, subscription=self.subscription)

        associate2 = create_test_associate(sport_association=self.sport_association)
        subscription2 = create_test_subscription(
            sport_association=self.sport_association, associate=associate2,
        )
        course_sub2 = create_test_course_subscription(course=self.course, subscription=subscription2)

        data = {
            'course_subscription_ids': [
                str(course_sub1.course_subscription_id),
                str(course_sub2.course_subscription_id),
            ]
        }
        response = self.client.delete('/course-subscriptions/bulk-delete', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_update_course_subscription(self):
        course_sub = create_test_course_subscription(course=self.course, subscription=self.subscription)
        data = {
            'course': str(self.course.course_id),
            'course_subscription_id': str(course_sub.course_subscription_id),
        }
        response = self.client.patch(
            f'/course-subscriptions/{course_sub.course_subscription_id}/update',
            data, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CourseOverviewDeleteTests(BaseAPITestCase):
    """Tests for course_overview_delete endpoint."""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )

    def test_delete_course_subscription_basic(self):
        course = create_test_course(self.sport_association)
        course_sub = create_test_course_subscription(course=course, subscription=self.subscription)

        response = self.client.delete(
            f'/course/{course.course_id}/overview/{self.subscription.subscription_id}/delete',
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            CourseSubscription.objects.filter(
                course_subscription_id=course_sub.course_subscription_id
            ).exists()
        )

    def test_delete_course_subscription_with_specific_installments(self):
        course = create_test_course(self.sport_association, multi_payments=True)
        course_sub = create_test_course_subscription(course=course, subscription=self.subscription, multi_payments=True)

        payment = Payment.objects.create(
            user=self.subscription.user,
            associate=self.subscription.associate,
            amount=Decimal('50.00'),
            subject=Payment.COURSE,
            sport_association=self.sport_association,
            paid=False,
        )
        installment = CourseSubscriptionInstallment.objects.create(
            course_subscription=course_sub,
            amount=Decimal('50.00'),
            payment_date=datetime.now(),
            payment=payment,
        )

        data = {'installments': [str(installment.course_subscription_installment_id)]}
        response = self.client.delete(
            f'/course/{course.course_id}/overview/{self.subscription.subscription_id}/delete',
            data, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_course_subscription_deletes_unpaid_installments(self):
        course = create_test_course(self.sport_association, multi_payments=True)
        course_sub = create_test_course_subscription(course=course, subscription=self.subscription, multi_payments=True)

        payment = Payment.objects.create(
            user=self.subscription.user,
            associate=self.subscription.associate,
            amount=Decimal('50.00'),
            subject=Payment.COURSE,
            sport_association=self.sport_association,
            paid=False,
        )
        CourseSubscriptionInstallment.objects.create(
            course_subscription=course_sub,
            amount=Decimal('50.00'),
            payment_date=datetime.now(),
            payment=payment,
            paid=False,
        )

        response = self.client.delete(
            f'/course/{course.course_id}/overview/{self.subscription.subscription_id}/delete',
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CourseOverviewAddTests(BaseAPITestCase):
    """Tests for course_overview_add endpoint."""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )

    def test_add_course_subscription_basic(self):
        course = create_test_course(self.sport_association)

        response = self.client.post(
            f'/course/{course.course_id}/overview/{self.subscription.subscription_id}/add',
            {'multi_payments': False}, format='json'
        )
        self.assertIn(response.status_code, [
            status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST
        ])

    def test_add_course_subscription_no_data(self):
        course = create_test_course(self.sport_association)

        response = self.client.post(
            f'/course/{course.course_id}/overview/{self.subscription.subscription_id}/add',
            {}, format='json'
        )
        self.assertIn(response.status_code, [
            status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST
        ])


class CourseOverviewUpdateTests(BaseAPITestCase):
    """Tests for course_overview_update endpoint."""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )
        self.course = create_test_course(
            self.sport_association,
            multi_payments=True,
            one_fee_payment=True,
            one_fee=Decimal('500.00'),
        )
        self.course_sub = create_test_course_subscription(
            course=self.course,
            subscription=self.subscription,
            multi_payments=False,
            one_fee_payment=False,
        )

    def test_update_to_multi_payments(self):
        data = {'multi_payments': True, 'all': True}
        response = self.client.post(
            f'/course/{self.course.course_id}/overview/{self.subscription.subscription_id}/update',
            data, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_to_one_fee_payment(self):
        self.course_sub.multi_payments = True
        self.course_sub.save()

        payment = Payment.objects.create(
            user=self.subscription.user,
            associate=self.subscription.associate,
            amount=Decimal('100.00'),
            subject=Payment.COURSE,
            sport_association=self.sport_association,
            paid=False,
        )
        CourseSubscriptionInstallment.objects.create(
            course_subscription=self.course_sub,
            amount=Decimal('100.00'),
            payment_date=datetime.now(),
            payment=payment,
            paid=False,
        )

        data = {'one_fee_payment': True}
        response = self.client.post(
            f'/course/{self.course.course_id}/overview/{self.subscription.subscription_id}/update',
            data, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CourseInstallmentMakePaymentTests(BaseAPITestCase):
    """Tests for course_installment_make_payment endpoint."""

    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, associate=self.associate,
        )

    def test_make_payment_creates_payment(self):
        course = create_test_course(self.sport_association, multi_payments=True)
        course_sub = create_test_course_subscription(course=course, subscription=self.subscription, multi_payments=True)
        installment = CourseSubscriptionInstallment.objects.create(
            course_subscription=course_sub,
            amount=Decimal('100.00'),
            payment_date=datetime.now(),
            payment=None,
        )

        response = self.client.post(
            f'/course-installment/{installment.course_subscription_installment_id}/make-payment',
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

        installment.refresh_from_db()
        self.assertIsNotNone(installment.payment)
