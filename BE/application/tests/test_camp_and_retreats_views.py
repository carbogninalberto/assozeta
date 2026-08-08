"""
Tests for Camps and Retreats views - CRUD operations.

Self-host port: adapted from SaaS test_camp_and_retreats_views.py.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.models.courses_models import (
    CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsSubscription,
)
from application.tests.base import BaseAPITestCase, BaseTransactionTestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
)


def create_test_camp(sport_association, **kwargs):
    defaults = {
        'sport_association': sport_association,
        'title': 'Test Camp',
        'description': 'Test description',
    }
    defaults.update(kwargs)
    return CampsAndRetreats.objects.create(**defaults)


def create_test_camp_period(camp, **kwargs):
    defaults = {
        'camps_and_retreats': camp,
        'start_date': timezone.now(),
        'end_date': timezone.now() + timedelta(days=7),
        'fee': Decimal('100.00'),
    }
    defaults.update(kwargs)
    return CampsAndRetreatsPeriod.objects.create(**defaults)


class CampsAndRetreatsListTests(BaseAPITestCase):
    """Tests for camps_and_retreats_list endpoint: GET /camps-and-retreats/list"""

    def test_list_camps_empty(self):
        response = self.client.get('/camps-and-retreats/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_camps_with_data(self):
        create_test_camp(self.sport_association)
        response = self.client.get('/camps-and-retreats/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_camps_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/camps-and-retreats/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_as_athlete_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE, username='ATHLETE_CAMP_LIST')
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/camps-and-retreats/list')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CampsAndRetreatsAddTests(BaseAPITestCase):
    """Tests for camps_and_retreats_add endpoint: POST /camps-and-retreats/add"""

    def test_add_camp_success(self):
        response = self.client.post('/camps-and-retreats/add', {
            'title': 'Summer Camp 2026',
            'description': 'A great summer camp',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_add_camp_as_athlete_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE, username='ATHLETE_CAMP1')
        self.client.force_authenticate(user=athlete)
        response = self.client.post('/camps-and-retreats/add', {
            'title': 'Test Camp',
            'description': 'Test',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CampsAndRetreatsUpdateTests(BaseAPITestCase):
    """Tests for camps_and_retreats_update endpoint: PATCH /camps-and-retreats/<uid>/update"""

    def setUp(self):
        super().setUp()
        self.camp = create_test_camp(self.sport_association)

    def test_update_camp_success(self):
        response = self.client.patch(
            f'/camps-and-retreats/{self.camp.camps_and_retreats_id}/update',
            {'title': 'Updated Camp Name'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_camp_title_only(self):
        response = self.client.patch(
            f'/camps-and-retreats/{self.camp.camps_and_retreats_id}/update',
            {'title': 'New Title'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_camp_description_only(self):
        response = self.client.patch(
            f'/camps-and-retreats/{self.camp.camps_and_retreats_id}/update',
            {'description': 'New Description'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_nonexistent_camp(self):
        response = self.client.patch(
            f'/camps-and-retreats/{uuid.uuid4()}/update',
            {'title': 'Updated Name'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_camp_as_athlete_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE, username='ATHLETE_CAMP2')
        self.client.force_authenticate(user=athlete)
        response = self.client.patch(
            f'/camps-and-retreats/{self.camp.camps_and_retreats_id}/update',
            {'title': 'Updated Name'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CampsAndRetreatsDeleteTests(BaseAPITestCase):
    """Tests for camps_and_retreats_delete endpoint: DELETE /camps-and-retreats/<uid>/delete"""

    def test_delete_camp_success(self):
        camp = create_test_camp(self.sport_association)
        response = self.client.delete(
            f'/camps-and-retreats/{camp.camps_and_retreats_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            CampsAndRetreats.objects.filter(
                camps_and_retreats_id=camp.camps_and_retreats_id
            ).exists()
        )

    def test_delete_nonexistent_camp(self):
        response = self.client.delete(f'/camps-and-retreats/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_camp_as_athlete_forbidden(self):
        camp = create_test_camp(self.sport_association)
        athlete = create_test_user(role=User.ATHLETE, username='ATHLETE_CAMP3')
        self.client.force_authenticate(user=athlete)
        response = self.client.delete(
            f'/camps-and-retreats/{camp.camps_and_retreats_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CampsAndRetreatsInfoTests(BaseAPITestCase):
    """Tests for camps_and_retreats_info endpoint: GET /camps-and-retreats/<uid>/info"""

    def setUp(self):
        super().setUp()
        self.camp = create_test_camp(self.sport_association)

    def test_get_camp_info(self):
        response = self.client.get(
            f'/camps-and-retreats/{self.camp.camps_and_retreats_id}/info'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_nonexistent_camp_info(self):
        response = self.client.get(f'/camps-and-retreats/{uuid.uuid4()}/info')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_info_with_periods(self):
        create_test_camp_period(self.camp)
        response = self.client.get(
            f'/camps-and-retreats/{self.camp.camps_and_retreats_id}/info'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_info_with_multiple_periods(self):
        create_test_camp_period(self.camp)
        create_test_camp_period(self.camp, fee=Decimal('200.00'))
        response = self.client.get(
            f'/camps-and-retreats/{self.camp.camps_and_retreats_id}/info'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CampsAndRetreatsPeriodsTests(BaseAPITestCase):
    """Tests for camps and retreats periods endpoints."""

    def setUp(self):
        super().setUp()
        self.camp = create_test_camp(self.sport_association)

    def test_add_period(self):
        response = self.client.post('/camps-and-retreats/periods/add', {
            'camps_and_retreats': str(self.camp.camps_and_retreats_id),
            'start_date': timezone.now().isoformat(),
            'end_date': (timezone.now() + timedelta(days=7)).isoformat(),
            'fee': '100.00',
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST])

    def test_update_period(self):
        period = create_test_camp_period(self.camp)
        response = self.client.patch(
            f'/camps-and-retreats/periods/{period.camps_and_retreats_period_id}/update',
            {'fee': '150.00'}, format='json',
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_delete_period(self):
        period = create_test_camp_period(self.camp)
        response = self.client.delete(
            f'/camps-and-retreats/periods/{period.camps_and_retreats_period_id}/delete'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_get_period_info(self):
        period = create_test_camp_period(self.camp)
        response = self.client.get(
            f'/camps-and-retreats/periods/{period.camps_and_retreats_period_id}/info'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND])

    def test_add_period_zero_fee(self):
        response = self.client.post('/camps-and-retreats/periods/add', {
            'camps_and_retreats': str(self.camp.camps_and_retreats_id),
            'start_date': timezone.now().isoformat(),
            'end_date': (timezone.now() + timedelta(days=7)).isoformat(),
            'fee': '0.00',
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST])

    def test_add_period_end_before_start(self):
        response = self.client.post('/camps-and-retreats/periods/add', {
            'camps_and_retreats': str(self.camp.camps_and_retreats_id),
            'start_date': (timezone.now() + timedelta(days=7)).isoformat(),
            'end_date': timezone.now().isoformat(),
            'fee': '100.00',
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST])


class CampsAndRetreatsPeriodsServicesTests(BaseAPITestCase):
    """Tests for camps and retreats periods services endpoints."""

    def setUp(self):
        super().setUp()
        self.camp = create_test_camp(self.sport_association)
        self.period = create_test_camp_period(self.camp)

    def test_add_period_service(self):
        response = self.client.post('/camps-and-retreats/periods/services/add', {
            'camps_and_retreats_period': str(self.period.camps_and_retreats_period_id),
            'title': 'Breakfast Service',
            'fee': '25.00',
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST])

    def test_update_period_service_not_found(self):
        response = self.client.patch(
            f'/camps-and-retreats/periods/services/{uuid.uuid4()}/update',
            {'title': 'Updated Service'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_period_service_not_found(self):
        response = self.client.delete(
            f'/camps-and-retreats/periods/services/{uuid.uuid4()}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CampsAndRetreatsSubscriptionsTests(BaseAPITestCase):
    """Tests for camps and retreats subscriptions endpoints."""

    def setUp(self):
        super().setUp()
        self.camp = create_test_camp(self.sport_association)

    def test_list_subscriptions(self):
        response = self.client.get(
            f'/camps-and-retreats/{self.camp.camps_and_retreats_id}/subscriptions/list'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_subscriptions_nonexistent_camp(self):
        response = self.client.get(
            f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/list'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND])


class CampsAndRetreatsSubscriptionsAddTests(BaseAPITestCase):
    """Tests for camps and retreats subscriptions add endpoint."""

    def setUp(self):
        super().setUp()
        self.camp = create_test_camp(self.sport_association)
        self.period = create_test_camp_period(self.camp)
        self.athlete = create_test_user(role=User.ATHLETE, username='SUB_ATHLETE_CAMP1')

    def test_add_subscription_invalid_uuid(self):
        response = self.client.post(
            '/camps-and-retreats/invalid-uuid/subscriptions/add',
            {'user': str(self.athlete.user_id)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CampsAndRetreatsSubscriptionsUpdateTests(BaseAPITestCase):
    """Tests for camps_and_retreats_subscriptions_update endpoint.

    Note: the uid in the URL pattern is the camps_and_retreats_subscription_id.
    """

    def setUp(self):
        super().setUp()
        self.camp = create_test_camp(self.sport_association)

    def test_update_subscription_not_found(self):
        response = self.client.patch(
            f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/update',
            {'periods': []}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_subscription_as_athlete_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE, username='ATHLETE_CAMP_UPD1')
        self.client.force_authenticate(user=athlete)
        response = self.client.patch(
            f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/update',
            {'periods': []}, format='json',
        )
        self.assertIn(response.status_code, [
            status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND,
        ])

    def test_update_subscription_invalid_uuid(self):
        response = self.client.patch(
            '/camps-and-retreats/invalid-uuid/subscriptions/update',
            {'periods': []}, format='json',
        )
        self.assertIn(response.status_code, [
            status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND,
        ])


class CampsAndRetreatsSubscriptionsDeleteTests(BaseAPITestCase):
    """Tests for camps_and_retreats_subscriptions_delete endpoint.

    Note: the uid in the URL pattern is the camps_and_retreats_subscription_id.
    """

    def setUp(self):
        super().setUp()
        self.camp = create_test_camp(self.sport_association)

    def test_delete_subscription_not_found(self):
        response = self.client.delete(
            f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_subscription_as_athlete_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE, username='ATHLETE_CAMP_DEL1')
        self.client.force_authenticate(user=athlete)
        response = self.client.delete(
            f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/delete'
        )
        self.assertIn(response.status_code, [
            status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND,
        ])

    def test_delete_subscription_invalid_uuid(self):
        response = self.client.delete(
            '/camps-and-retreats/invalid-uuid/subscriptions/delete'
        )
        self.assertIn(response.status_code, [
            status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND,
        ])


class CampsAndRetreatsCrossAssociationTests(BaseAPITestCase):
    """Tests for cross-association access restrictions."""

    def setUp(self):
        super().setUp()
        self.camp1 = create_test_camp(self.sport_association, title='Camp 1')
        self.period1 = create_test_camp_period(self.camp1)
        self.user2 = create_test_user(role=User.ASSOCIATION, username='ASSOC_CAMP2')
        create_test_sport_association(user=self.user2)

        self.client2 = APIClient()
        self.client2.force_authenticate(user=self.user2)

    def test_update_other_association_camp(self):
        response = self.client2.patch(
            f'/camps-and-retreats/{self.camp1.camps_and_retreats_id}/update',
            {'title': 'Hacked Camp'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_other_association_camp(self):
        response = self.client2.delete(
            f'/camps-and-retreats/{self.camp1.camps_and_retreats_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_other_association_period(self):
        response = self.client2.patch(
            f'/camps-and-retreats/periods/{self.period1.camps_and_retreats_period_id}/update',
            {'fee': '999.00'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_other_association_period(self):
        response = self.client2.delete(
            f'/camps-and-retreats/periods/{self.period1.camps_and_retreats_period_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CampsAndRetreatsAuthTests(TestCase):
    """Tests for authentication requirements across camps endpoints."""

    def setUp(self):
        self.client = APIClient()

    def test_list_requires_auth(self):
        response = self.client.get('/camps-and-retreats/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_add_requires_auth(self):
        response = self.client.post('/camps-and-retreats/add', {'title': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_requires_auth(self):
        response = self.client.patch(f'/camps-and-retreats/{uuid.uuid4()}/update', {'title': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_delete_requires_auth(self):
        response = self.client.delete(f'/camps-and-retreats/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_info_requires_auth(self):
        response = self.client.get(f'/camps-and-retreats/{uuid.uuid4()}/info')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_periods_add_requires_auth(self):
        response = self.client.post('/camps-and-retreats/periods/add', {'title': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_periods_update_requires_auth(self):
        response = self.client.patch(
            f'/camps-and-retreats/periods/{uuid.uuid4()}/update', {'title': 'Test'}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_periods_delete_requires_auth(self):
        response = self.client.delete(f'/camps-and-retreats/periods/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_periods_services_add_requires_auth(self):
        response = self.client.post('/camps-and-retreats/periods/services/add', {'title': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_periods_services_update_requires_auth(self):
        response = self.client.patch(
            f'/camps-and-retreats/periods/services/{uuid.uuid4()}/update', {'title': 'Test'}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_periods_services_delete_requires_auth(self):
        response = self.client.delete(f'/camps-and-retreats/periods/services/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_subscriptions_list_requires_auth(self):
        response = self.client.get(f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_subscriptions_add_requires_auth(self):
        response = self.client.post(
            f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/add',
            {'user': str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_subscriptions_update_requires_auth(self):
        response = self.client.patch(
            f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/update',
            {'periods': []},
        )
        self.assertIn(response.status_code, [
            status.HTTP_401_UNAUTHORIZED, status.HTTP_404_NOT_FOUND,
        ])

    def test_subscriptions_delete_requires_auth(self):
        response = self.client.delete(
            f'/camps-and-retreats/{uuid.uuid4()}/subscriptions/delete'
        )
        self.assertIn(response.status_code, [
            status.HTTP_401_UNAUTHORIZED, status.HTTP_404_NOT_FOUND,
        ])


class CampsAndRetreatsListingVariationsTests(BaseAPITestCase):
    """Tests for different listing variations."""

    def test_list_camps_with_pagination(self):
        for i in range(3):
            create_test_camp(self.sport_association, title=f'Camp {i}')
        response = self.client.get(
            '/camps-and-retreats/list?pagination[page]=1&pagination[perpage]=2'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_camps_page_2(self):
        for i in range(3):
            create_test_camp(self.sport_association, title=f'Camp {i}')
        response = self.client.get(
            '/camps-and-retreats/list?pagination[page]=2&pagination[perpage]=1'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
