from rest_framework.test import APIClient

from application.models import User
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.payment_models import Payment
from application.tests.base import BaseTransactionTestCase
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_course,
    create_test_course_subscription,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)
from application.tests.test_carnet_views import create_test_carnet, create_test_carnet_subscription


class CarnetAssociationScopeTests(BaseTransactionTestCase):
    def setUp(self):
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.own = create_test_sport_association(user=self.owner)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.own_carnet = create_test_carnet(self.own, title='Own')
        self.own_sub, self.own_cs, self.own_csub = self.enrolled(self.own, self.own_carnet)
        foreign = create_test_sport_association(user=create_test_user(role=User.ASSOCIATION))
        self.carnet = create_test_carnet(foreign, title='Foreign')
        self.sub, self.cs, self.csub = self.enrolled(foreign, self.carnet)

    def enrolled(self, association, carnet):
        athlete = create_test_user(role=User.ATHLETE)
        associate = create_test_associate(user=athlete, sport_association=association)
        subscription = create_test_subscription(user=athlete, associate=associate, sport_association=association)
        course_subscription = create_test_course_subscription(
            course=create_test_course(sport_association=association), subscription=subscription)
        carnet_subscription = create_test_carnet_subscription(carnet, subscription)
        carnet_subscription.course_subscription.add(course_subscription)
        return subscription, course_subscription, carnet_subscription

    def update_body(self, title):
        return {'title': title, 'description': 'd', 'lessons_number': 1, 'fee': '1.00', 'public': True}

    def test_foreign_carnet_update_is_not_found_and_unchanged(self):
        response = self.client.patch(f'/carnet/{self.carnet.pk}/update', self.update_body('X'), format='json')
        self.assertEqual((response.status_code, response.data), (404, {'msg': 'Carnet not found.'}))
        self.carnet.refresh_from_db()
        self.assertEqual(self.carnet.title, 'Foreign')
        response = self.client.patch(f'/carnet/{self.own_carnet.pk}/update', self.update_body('Mine'), format='json')
        self.assertEqual(response.status_code, 200)
        self.own_carnet.refresh_from_db()
        self.assertEqual(self.own_carnet.title, 'Mine')

    def test_foreign_carnet_delete_is_not_found_and_kept(self):
        response = self.client.post(f'/carnet/{self.carnet.pk}/delete')
        self.assertEqual((response.status_code, response.data), (404, {'msg': 'Carnet not found.'}))
        self.assertTrue(Carnet.objects.filter(pk=self.carnet.pk).exists())
        self.assertEqual(self.client.post(f'/carnet/{self.own_carnet.pk}/delete').status_code, 200)
        self.assertFalse(Carnet.objects.filter(pk=self.own_carnet.pk).exists())

    def test_foreign_carnet_subscription_course_link_is_kept(self):
        response = self.client.delete(f'/carnet-subscription/{self.csub.pk}/delete/{self.cs.pk}')
        self.assertEqual((response.status_code, response.data), (404, {'msg': 'Carnet subscription not found.'}))
        self.assertEqual(self.csub.course_subscription.count(), 1)
        self.assertEqual(self.client.delete(f'/carnet-subscription/{self.own_csub.pk}/delete/{self.own_cs.pk}').status_code, 200)
        self.assertEqual(self.own_csub.course_subscription.count(), 0)

    def test_foreign_unassign_keeps_carnet_subscription_and_payment(self):
        payment = self.csub.payment
        response = self.client.delete(f'/carnet/{self.carnet.pk}/unassign/{self.csub.pk}')
        self.assertEqual((response.status_code, response.data), (404, {'msg': 'Carnet not found.'}))
        self.assertTrue(CarnetSubscription.objects.filter(pk=self.csub.pk).exists())
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())
        self.assertEqual(self.client.delete(f'/carnet/{self.own_carnet.pk}/unassign/{self.own_csub.pk}').status_code, 200)
        self.assertFalse(CarnetSubscription.objects.filter(pk=self.own_csub.pk).exists())

    def test_own_carnet_is_not_assigned_to_foreign_subscription(self):
        payments = Payment.objects.count()
        response = self.client.post(f'/carnet/{self.own_carnet.pk}/assign/{self.sub.pk}', {}, format='json')
        self.assertEqual((response.status_code, response.data), (404, {'msg': 'Subscription not found.'}))
        self.assertFalse(CarnetSubscription.objects.filter(subscription=self.sub, carnet_id=self.own_carnet).exists())
        self.assertEqual(Payment.objects.count(), payments)
        response = self.client.post(f'/carnet/{self.own_carnet.pk}/assign/{self.own_sub.pk}', {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CarnetSubscription.objects.filter(subscription=self.own_sub).count(), 2)

    def test_assign_does_not_link_foreign_carnet_or_course_subscriptions(self):
        response = self.client.post(f'/carnet/{self.own_carnet.pk}/assign/{self.own_sub.pk}',
                                    {'carnet_subscription_id': str(self.csub.pk), 'course_subscription_id': str(self.own_cs.pk)},
                                    format='json')
        self.assertEqual((response.status_code, response.data), (404, {'msg': 'Carnet subscription not found.'}))
        self.assertEqual(list(self.csub.course_subscription.all()), [self.cs])
        response = self.client.post(f'/carnet/{self.own_carnet.pk}/assign/{self.own_sub.pk}',
                                    {'carnet_subscription_id': str(self.own_csub.pk), 'course_subscription_id': str(self.cs.pk)},
                                    format='json')
        self.assertEqual((response.status_code, response.data), (404, {'msg': 'Course subscription not found.'}))
        self.assertEqual(list(self.own_csub.course_subscription.all()), [self.own_cs])

    def test_add_skips_foreign_subscriptions(self):
        response = self.client.post('/carnet/add', {'title': 'New', 'description': 'd', 'lessons_number': 2, 'fee': '3.00',
                                                    'public': True, 'subscriptions': [str(self.sub.pk), str(self.own_sub.pk)]},
                                    format='json')
        self.assertEqual(response.status_code, 200)
        carnet = Carnet.objects.get(title='New')
        self.assertEqual(list(CarnetSubscription.objects.filter(carnet_id=carnet).values_list('subscription', flat=True)),
                         [self.own_sub.pk])

    def test_foreign_subscription_carnet_list_is_not_found(self):
        response = self.client.get(f'/carnet-subscription/list?subscription_id={self.sub.pk}')
        self.assertEqual((response.status_code, response.data), (404, {'msg': 'Subscription not found.'}))
        response = self.client.get(f'/carnet-subscription/list?subscription_id={self.own_sub.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['carnet_subscription_id'] for row in response.data['data']], [self.own_csub.pk])
