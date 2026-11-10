"""Family matching by tax code: only a filled-in tax code links subscriptions."""
import datetime

from rest_framework import status
from rest_framework.test import APIClient

from application.models import User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)
from application.utils.subscriptions_utils import family_tax_codes
from application.models.subscriptions_models import Subscription


class FamilyTaxCodeMatchingTests(BaseAPITestCase):

    def setUp(self):
        super().setUp()
        today = datetime.date.today()
        self.season = {'start_date': today - datetime.timedelta(days=10), 'end_date': today + datetime.timedelta(days=10)}
        self.athlete = create_test_user(role=User.ATHLETE)
        self.athlete_client = APIClient()
        self.athlete_client.force_authenticate(user=self.athlete)

    def tearDown(self):
        self.athlete_client.force_authenticate(user=None)
        super().tearDown()

    def _own(self, tax_code, association=None):
        association = association or self.sport_association
        associate = create_test_associate(sport_association=association, user=self.athlete, tax_code=tax_code)
        return create_test_subscription(sport_association=association, associate=associate, user=self.athlete, **self.season)

    def _other(self, tax_code, association, **associate_fields):
        associate = create_test_associate(sport_association=association, tax_code=tax_code, **associate_fields)
        return create_test_subscription(sport_association=association, associate=associate, user=association.user, **self.season)

    def test_helper_keeps_only_filled_tax_codes(self):
        self._own('')
        self._own(None, association=create_test_sport_association())
        filled = self._own('RSSMRA80A01H501U', association=create_test_sport_association())
        codes = list(family_tax_codes(Subscription.objects.filter(user=self.athlete)))
        self.assertEqual(codes, [filled.associate.tax_code])

    def test_info_blank_tax_code_does_not_link_subscriptions(self):
        self._own('')
        other = self._other('', create_test_sport_association())
        response = self.athlete_client.get(f'/subscription/{other.subscription_id}/info')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_info_filled_tax_code_links_subscriptions(self):
        self._own('RSSMRA80A01H501U')
        other = self._other('RSSMRA80A01H501U', create_test_sport_association())
        response = self.athlete_client.get(f'/subscription/{other.subscription_id}/info')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_blank_tax_code_does_not_link_subscriptions(self):
        own = self._own('')
        self._other('', create_test_sport_association())
        response = self.athlete_client.get('/subscription/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        listed = [row['subscription_id'] for row in response.json()['data'].values()]
        self.assertEqual(listed, [str(own.subscription_id)])

    def test_list_filled_tax_code_links_subscriptions(self):
        own = self._own('RSSMRA80A01H501U')
        other = self._other('RSSMRA80A01H501U', create_test_sport_association())
        response = self.athlete_client.get('/subscription/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        listed = {row['subscription_id'] for row in response.json()['data'].values()}
        self.assertEqual(listed, {str(own.subscription_id), str(other.subscription_id)})

    def test_dashboard_blank_tax_code_does_not_link_subscriptions(self):
        self._own('')
        # Only a removed member can share a tax code inside one association.
        self._other('', self.sport_association, deleted=True)
        response = self.athlete_client.get('/statistic/athlete-dashboard')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['data'][0]['subscriptions'], 1)
