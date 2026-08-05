from django.test import TestCase
from rest_framework.test import APIClient

from application.models import SportAssociation, Subscription, User
from instance.models import InstanceConfiguration


class AthleteDashboardAssociationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username='primary-owner',
            email='primary-owner@example.com',
            password='StrongPass!1',
            role=User.ASSOCIATION,
        )
        self.primary_association = SportAssociation.objects.create(
            user=self.owner,
            denomination='Primary Association',
            tax_code='12345678901',
        )
        self.instance_config = InstanceConfiguration.objects.create(
            domain='selfhost.example.com',
            name='Self-hosted Instance',
            setup_provenance=InstanceConfiguration.SETUP_PROVENANCE_FRESH,
            primary_association=self.primary_association,
            self_hosted=True,
            support_multiple_associations=False,
        )
        self.athlete = User.objects.create_user(
            username='athlete',
            email='athlete@example.com',
            password='StrongPass!1',
            role=User.ATHLETE,
        )
        self.client.force_authenticate(user=self.athlete)

    def test_athlete_without_subscriptions_sees_primary_association(self):
        response = self.client.get('/statistic/athlete-dashboard')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.data['data']), 1)
        dashboard_association = response.data['data'][0]
        self.assertEqual(
            str(dashboard_association['sport_association']['sport_association_id']),
            str(self.primary_association.sport_association_id),
        )
        self.assertEqual(dashboard_association['subscriptions'], 0)
        self.assertEqual(response.data['upcoming_lessons'], [])

    def test_secondary_association_data_is_never_shown_or_counted(self):
        secondary_owner = User.objects.create_user(
            username='secondary-owner',
            email='secondary-owner@example.com',
            password='StrongPass!1',
            role=User.ASSOCIATION,
        )
        secondary_association = SportAssociation.objects.create(
            user=secondary_owner,
            denomination='Legacy Secondary Association',
            tax_code='10987654321',
        )
        Subscription(
            sport_association=secondary_association,
            user=self.athlete,
        ).save()

        response = self.client.get('/statistic/athlete-dashboard')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.data['data']), 1)
        dashboard_association = response.data['data'][0]
        self.assertEqual(
            str(dashboard_association['sport_association']['sport_association_id']),
            str(self.primary_association.sport_association_id),
        )
        self.assertEqual(dashboard_association['subscriptions'], 0)

    def test_primary_association_subscription_is_counted(self):
        Subscription(
            sport_association=self.primary_association,
            user=self.athlete,
        ).save()

        response = self.client.get('/statistic/athlete-dashboard')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['data'][0]['subscriptions'], 1)

    def test_missing_primary_association_returns_configuration_error(self):
        self.instance_config.primary_association = None
        self.instance_config.save(update_fields=['primary_association'])

        response = self.client.get('/statistic/athlete-dashboard')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data['error'],
            'Primary sport association is not configured.',
        )

    def test_association_user_cannot_open_athlete_dashboard(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get('/statistic/athlete-dashboard')

        self.assertEqual(response.status_code, 403)
