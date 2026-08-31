from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from application.tests.base import BaseTestCase
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_medical_certificate,
    create_test_sport_association,
    create_test_subscription,
)
from application.serializers.subscriptions_serializers import SubscriptionInfoSerializer


class SubscriptionMedicalLabelTests(BaseTestCase):
    def setUp(self):
        self.sport_association = create_test_sport_association()
        self.associate = create_test_associate(
            sport_association=self.sport_association,
            born_date=date(1990, 1, 1),
        )

    @patch('application.models.subscriptions_models.timezone.now')
    def test_missing_certificate_uses_rome_local_enrollment_date(self, now_mock):
        now_mock.return_value = datetime(2026, 8, 31, 10, 0, tzinfo=dt_timezone.utc)
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
            creation_date=datetime(2026, 8, 28, 23, 30, tzinfo=dt_timezone.utc),
        )

        self.assertEqual(subscription.get_plain_medical_label(), 'Mancante da 2 giorni')
        self.assertIn('Mancante da 2 giorni', subscription.get_medical_label())

    @patch('application.models.subscriptions_models.timezone.now')
    def test_missing_certificate_uses_singular_and_clamps_future_enrollment(self, now_mock):
        now_mock.return_value = datetime(2026, 8, 31, 10, 0, tzinfo=dt_timezone.utc)
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
            creation_date=datetime(2026, 8, 30, 10, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(subscription.get_plain_medical_label(), 'Mancante da 1 giorno')

        subscription.creation_date = datetime(2026, 9, 1, 10, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(subscription.get_plain_medical_label(), 'Mancante da 0 giorni')

    @patch('application.models.subscriptions_models.timezone.now')
    def test_certificate_without_expiration_uses_the_same_missing_label(self, now_mock):
        now_mock.return_value = datetime(2026, 8, 31, 10, 0, tzinfo=dt_timezone.utc)
        medical = create_test_medical_certificate(
            user=self.sport_association.user,
            expiration_date=None,
            competitive_medical_certificate=True,
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
            medical=medical,
            creation_date=datetime(2026, 8, 26, 10, 0, tzinfo=dt_timezone.utc),
        )

        self.assertEqual(subscription.get_plain_medical_label(), 'Mancante da 5 giorni')
        html_label = subscription.get_medical_label()
        self.assertIn('Mancante da 5 giorni', html_label)
        self.assertIn('>AG</span>', html_label)

    @patch('application.models.subscriptions_models.timezone.now')
    def test_subscription_detail_serializer_exposes_missing_certificate_countdown(self, now_mock):
        now_mock.return_value = datetime(2026, 8, 31, 10, 0, tzinfo=dt_timezone.utc)
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
            creation_date=datetime(2026, 2, 10, 10, 0, tzinfo=dt_timezone.utc),
        )

        data = SubscriptionInfoSerializer(subscription).data

        self.assertEqual(data['plain_medical_label'], 'Mancante da 202 giorni')
