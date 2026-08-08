"""
Tests for Carnet views - carnet management endpoints.

Self-host port: adapted from SaaS test_carnet_views.py.
"""
import uuid
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.courses_models import Course, CourseSubscription
from application.models.payment_models import Payment
from application.tests.base import BaseTransactionTestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_associate,
    create_test_subscription,
    create_test_course,
    create_test_course_subscription,
    create_test_payment,
)


def create_test_carnet(sport_association, **kwargs):
    defaults = {
        'sport_association': sport_association,
        'title': 'Test Carnet',
        'description': 'Test carnet description',
        'lessons_number': 10,
        'fee': Decimal('50.00'),
        'public': True,
    }
    defaults.update(kwargs)
    return Carnet.objects.create(**defaults)


def create_test_carnet_subscription(carnet, subscription, user=None, payment=None, **kwargs):
    if not payment:
        payment = create_test_payment(
            user=subscription.user,
            associate=subscription.associate,
            amount=carnet.fee,
            subject=Payment.COURSE,
            sport_association=carnet.sport_association,
        )

    defaults = {
        'carnet_id': carnet,
        'user_id': user or subscription.user,
        'subscription': subscription,
        'payment': payment,
        'disabled': False,
        'meta': {
            'lessons_counter': carnet.lessons_number,
            'lessons_left': carnet.lessons_number,
            'lessons_registry': []
        }
    }
    defaults.update(kwargs)
    return CarnetSubscription.objects.create(**defaults)


class CarnetListTests(BaseTransactionTestCase):
    """Tests for carnet_list endpoint: GET /carnet/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_carnets_empty(self):
        response = self.client.get('/carnet/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertEqual(data['data'], [])

    def test_list_carnets_with_data(self):
        for i in range(3):
            create_test_carnet(self.sport_association, title=f'Carnet {i + 1}')
        response = self.client.get('/carnet/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['data']), 3)

    def test_list_carnets_as_athlete(self):
        athlete_user = create_test_user(role=User.ATHLETE)
        associate = create_test_associate(
            sport_association=self.sport_association, user=athlete_user,
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate, user=athlete_user,
        )
        carnet = create_test_carnet(self.sport_association)
        create_test_carnet_subscription(carnet, subscription, user=athlete_user)

        self.client.force_authenticate(user=athlete_user)
        response = self.client.get('/carnet/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data['data']), 1)

    def test_carnet_list_requires_auth(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/carnet/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CarnetAddTests(BaseTransactionTestCase):
    """Tests for carnet_add endpoint: POST /carnet/add"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_add_carnet_success(self):
        data = {
            'title': 'New Carnet',
            'description': 'New carnet description',
            'lessons_number': 10,
            'fee': '50.00',
            'public': True,
            'subscriptions': []
        }
        response = self.client.post('/carnet/add', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Carnet.objects.filter(title='New Carnet').exists())

    def test_add_carnet_with_subscription(self):
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        data = {
            'title': 'New Carnet',
            'description': 'New carnet description',
            'lessons_number': 10,
            'fee': '50.00',
            'public': True,
            'subscriptions': [str(subscription.subscription_id)]
        }
        response = self.client.post('/carnet/add', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_add_carnet_as_non_association_forbidden(self):
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        data = {
            'title': 'New Carnet', 'description': 'New carnet description',
            'lessons_number': 10, 'fee': '50.00', 'public': True,
            'subscriptions': []
        }
        response = self.client.post('/carnet/add', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_add_carnet_invalid_data(self):
        data = {
            'title': '',
            'description': 'Description',
            'lessons_number': 10,
            'fee': '50.00',
            'public': True,
            'subscriptions': []
        }
        response = self.client.post('/carnet/add', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CarnetUpdateTests(BaseTransactionTestCase):
    """Tests for carnet_update endpoint: PATCH /carnet/<uid>/update"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_carnet_success(self):
        carnet = create_test_carnet(self.sport_association)
        data = {
            'title': 'Updated Carnet',
            'description': 'Updated description',
            'lessons_number': 20,
            'fee': '75.00',
            'public': False,
        }
        response = self.client.patch(
            f'/carnet/{carnet.carnet_id}/update', data, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        carnet.refresh_from_db()
        self.assertEqual(carnet.title, 'Updated Carnet')
        self.assertEqual(carnet.lessons_number, 20)

    def test_update_carnet_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        data = {'title': 'Updated'}
        response = self.client.patch(
            f'/carnet/{carnet.carnet_id}/update', data, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_nonexistent_carnet(self):
        response = self.client.patch(
            f'/carnet/{uuid.uuid4()}/update',
            {'title': 'Test', 'description': 'Desc', 'lessons_number': 10,
             'fee': '50.00', 'public': True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CarnetDeleteTests(BaseTransactionTestCase):
    """Tests for carnet_delete endpoint: POST /carnet/<uid>/delete"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_delete_carnet_success(self):
        carnet = create_test_carnet(self.sport_association)
        carnet_id = carnet.carnet_id
        response = self.client.post(f'/carnet/{carnet_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Carnet.objects.filter(carnet_id=carnet_id).exists())

    def test_delete_carnet_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.post(f'/carnet/{carnet.carnet_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_nonexistent_carnet(self):
        response = self.client.post(f'/carnet/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CarnetInfoTests(BaseTransactionTestCase):
    """Tests for carnet_info endpoint: GET /carnet/<uid>/info"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_get_carnet_info_success(self):
        carnet = create_test_carnet(self.sport_association)
        response = self.client.get(f'/carnet/{carnet.carnet_id}/info')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertEqual(data['data']['title'], carnet.title)

    def test_get_carnet_info_with_subscriptions(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        create_test_carnet_subscription(carnet, subscription)
        response = self.client.get(f'/carnet/{carnet.carnet_id}/info')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('subscriptions', data['data'])

    def test_get_carnet_info_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.get(f'/carnet/{carnet.carnet_id}/info')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_nonexistent_carnet_info(self):
        response = self.client.get(f'/carnet/{uuid.uuid4()}/info')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CarnetAssignTests(BaseTransactionTestCase):
    """Tests for carnet_assign endpoint: POST /carnet/<uid>/assign/<uid_subscription>"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_assign_carnet_success(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        response = self.client.post(
            f'/carnet/{carnet.carnet_id}/assign/{subscription.subscription_id}',
            {}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            CarnetSubscription.objects.filter(
                carnet_id=carnet, subscription=subscription
            ).exists()
        )

    def test_assign_carnet_nonexistent_subscription(self):
        carnet = create_test_carnet(self.sport_association)
        response = self.client.post(
            f'/carnet/{carnet.carnet_id}/assign/{uuid.uuid4()}',
            {}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_assign_nonexistent_carnet(self):
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        response = self.client.post(
            f'/carnet/{uuid.uuid4()}/assign/{subscription.subscription_id}',
            {}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CarnetUnassignTests(BaseTransactionTestCase):
    """Tests for carnet_unassign endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_unassign_carnet_success(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        response = self.client.delete(
            f'/carnet/{carnet.carnet_id}/unassign/{carnet_sub.carnet_subscription_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            CarnetSubscription.objects.filter(
                carnet_subscription_id=carnet_sub.carnet_subscription_id
            ).exists()
        )

    def test_unassign_carnet_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.delete(
            f'/carnet/{carnet.carnet_id}/unassign/{carnet_sub.carnet_subscription_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unassign_nonexistent_carnet_subscription(self):
        carnet = create_test_carnet(self.sport_association)
        response = self.client.delete(
            f'/carnet/{carnet.carnet_id}/unassign/{uuid.uuid4()}'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CarnetSubscriptionListTests(BaseTransactionTestCase):
    """Tests for carnet_subscription_list endpoint: GET /carnet-subscription/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_carnet_subscriptions_with_subscription_id(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        create_test_carnet_subscription(carnet, subscription)
        response = self.client.get(
            f'/carnet-subscription/list?subscription_id={subscription.subscription_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertEqual(len(data['data']), 1)

    def test_list_carnet_subscriptions_as_non_owner_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        create_test_carnet_subscription(carnet, subscription)

        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.get(
            f'/carnet-subscription/list?subscription_id={subscription.subscription_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CarnetSubscriptionDisableTests(BaseTransactionTestCase):
    """Tests for carnet_subscription_disable endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_disable_carnet_subscription_success(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/disable'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        carnet_sub.refresh_from_db()
        self.assertTrue(carnet_sub.disabled)

    def test_disable_carnet_subscription_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/disable'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CarnetSubscriptionEnableTests(BaseTransactionTestCase):
    """Tests for carnet_subscription_enable endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_enable_carnet_subscription_success(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription, disabled=True)
        carnet_sub.disabled = True
        carnet_sub.save()
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/enable'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        carnet_sub.refresh_from_db()
        self.assertFalse(carnet_sub.disabled)

    def test_enable_carnet_subscription_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/enable'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CarnetSubscriptionUpdateTests(BaseTransactionTestCase):
    """Tests for carnet_subscription_update endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_update_carnet_subscription_lessons_left(self):
        carnet = create_test_carnet(self.sport_association, lessons_number=10)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/update',
            {'lessons_left': 5}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        carnet_sub.refresh_from_db()
        self.assertEqual(carnet_sub.meta['lessons_left'], 5)

    def test_update_carnet_subscription_lessons_left_exceeds_max(self):
        carnet = create_test_carnet(self.sport_association, lessons_number=10)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/update',
            {'lessons_left': 15}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_carnet_subscription_negative_lessons_left(self):
        carnet = create_test_carnet(self.sport_association, lessons_number=10)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/update',
            {'lessons_left': -1}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_carnet_subscription_missing_lessons_left(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/update',
            {}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_carnet_subscription_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.patch(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/update',
            {'lessons_left': 5}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CarnetSubscriptionTopupTests(BaseTransactionTestCase):
    """Tests for carnet_subscription_topup endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_topup_carnet_subscription_as_association(self):
        carnet = create_test_carnet(self.sport_association, lessons_number=10)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        carnet_sub.meta['lessons_left'] = 0
        carnet_sub.save()

        response = self.client.post(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/topup'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            CarnetSubscription.objects.filter(carnet_id=carnet).count(), 2
        )

    def test_topup_carnet_subscription_as_athlete_owner(self):
        carnet = create_test_carnet(self.sport_association, lessons_number=10)
        athlete_user = create_test_user(role=User.ATHLETE)
        associate = create_test_associate(
            sport_association=self.sport_association, user=athlete_user,
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate, user=athlete_user,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription, user=athlete_user)
        self.client.force_authenticate(user=athlete_user)

        response = self.client.post(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/topup'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_topup_carnet_subscription_as_non_owner_forbidden(self):
        carnet = create_test_carnet(self.sport_association, lessons_number=10)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        carnet_sub = create_test_carnet_subscription(carnet, subscription)

        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.post(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/topup'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CarnetReplaceTests(BaseTransactionTestCase):
    """Tests for carnet_replace endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_replace_carnet_success(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        response = self.client.post(
            f'/carnet/{carnet.carnet_id}/replace/{subscription.subscription_id}',
            {}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_replace_carnet_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.post(
            f'/carnet/{carnet.carnet_id}/replace/{subscription.subscription_id}',
            {}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CarnetSubscriptionDeleteTests(BaseTransactionTestCase):
    """Tests for carnet_subscription_delete endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_delete_course_from_carnet_subscription(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        course = create_test_course(self.sport_association)
        course_sub = create_test_course_subscription(course=course, subscription=subscription)
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        carnet_sub.course_subscription.add(course_sub)

        response = self.client.delete(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/delete/{course_sub.course_subscription_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        carnet_sub.refresh_from_db()
        self.assertFalse(carnet_sub.course_subscription.filter(
            course_subscription_id=course_sub.course_subscription_id
        ).exists())

    def test_delete_course_as_non_association_forbidden(self):
        carnet = create_test_carnet(self.sport_association)
        associate = create_test_associate(sport_association=self.sport_association)
        subscription = create_test_subscription(
            sport_association=self.sport_association, associate=associate,
        )
        course = create_test_course(self.sport_association)
        course_sub = create_test_course_subscription(course=course, subscription=subscription)
        carnet_sub = create_test_carnet_subscription(carnet, subscription)
        carnet_sub.course_subscription.add(course_sub)

        athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete_user)
        response = self.client.delete(
            f'/carnet-subscription/{carnet_sub.carnet_subscription_id}/delete/{course_sub.course_subscription_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CarnetAuthTests(BaseTransactionTestCase):
    """Tests for authentication requirements across carnet endpoints."""

    def setUp(self):
        self.client = APIClient()

    def test_carnet_list_requires_auth(self):
        response = self.client.get('/carnet/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_carnet_add_requires_auth(self):
        response = self.client.post('/carnet/add', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_carnet_info_requires_auth(self):
        response = self.client.get(f'/carnet/{uuid.uuid4()}/info')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_carnet_update_requires_auth(self):
        response = self.client.patch(f'/carnet/{uuid.uuid4()}/update', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_carnet_delete_requires_auth(self):
        response = self.client.post(f'/carnet/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_carnet_assign_requires_auth(self):
        response = self.client.post(f'/carnet/{uuid.uuid4()}/assign/{uuid.uuid4()}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_carnet_subscription_list_requires_auth(self):
        response = self.client.get('/carnet-subscription/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_carnet_subscription_update_requires_auth(self):
        response = self.client.patch(f'/carnet-subscription/{uuid.uuid4()}/update', {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_carnet_subscription_topup_requires_auth(self):
        response = self.client.post(f'/carnet-subscription/{uuid.uuid4()}/topup')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
