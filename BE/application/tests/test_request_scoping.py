"""Requests name their rows by path or by id; values sent in the body never choose another row."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from auditlog.models import LogEntry
from django.test import TestCase
from rest_framework.test import APIClient

from application.models.payment_models import Payment
from application.models.user_models import SportAssociation, User
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_payment,
    create_test_sport_association,
    create_test_user,
)


class OwnedFixture(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.client.force_authenticate(user=self.user)

    def payment(self, **overrides):
        return create_test_payment(sport_association=self.sport_association, associate=self.associate, user=self.user, **overrides)


class PaymentUpdateKeyTests(OwnedFixture):
    def update(self, payment, body):
        return self.client.patch(f'/payment/{payment.payment_id}/update', {'creation_date': '', **body}, format='json')

    def test_body_key_naming_another_row_leaves_that_row_alone(self):
        mine = self.payment(amount=Decimal('10.00'))
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        foreign = create_test_payment(sport_association=other_association, user=other_user, amount=Decimal('20.00'))
        removed = self.payment(amount=Decimal('30.00'))
        removed.delete()
        for target in (foreign, removed):
            response = self.update(mine, {'payment_id': str(target.payment_id), 'amount': '99.00'})

            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()['data']['payment']['payment_id'], str(mine.payment_id))
            mine.refresh_from_db()
            self.assertEqual(mine.amount, Decimal('99.00'))
            mine.amount = Decimal('10.00')
            mine.save()
        self.assertEqual(Payment.all_objects.get(payment_id=foreign.payment_id).amount, Decimal('20.00')) \
            if hasattr(Payment, 'all_objects') else None
        foreign.refresh_from_db()
        self.assertEqual(foreign.amount, Decimal('20.00'))

    def test_same_key_and_unusable_key_are_ignored(self):
        mine = self.payment()
        for value in (str(mine.payment_id), 'not-a-uuid', None):
            response = self.update(mine, {'payment_id': value, 'description': f'v{value}'[:20]})
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Payment.objects.filter(payment_id=mine.payment_id).count(), 1)


class MultiplePayServerSideTests(OwnedFixture):
    def post(self, payments):
        with patch('application.views.stripe_views.online_payments_available', return_value=True), \
                patch('application.views.stripe_views.stripe_request_options', return_value={}), \
                patch('application.views.stripe_views.stripe.PaymentIntent.create',
                      return_value=SimpleNamespace(stripe_id='pi_test', client_secret='secret')) as create:
            return APIClient().post('/stripe/multiple-pay', {'payments': payments}, format='json'), create

    def test_objects_are_replaced_by_the_stored_rows(self):
        self.sport_association.checkout_info = 'stored info'
        self.sport_association.save()
        other_association = create_test_sport_association(user=create_test_user(role=User.ASSOCIATION))
        other_association.checkout_info = 'other info'
        other_association.save()
        payment = self.payment(amount=Decimal('12.00'), paid=False)

        response, create = self.post([{'payment_id': str(payment.payment_id),
                                       'sport_association': str(other_association.pk), 'amount': '0.01'}])

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['data']['info']['checkout_info'], 'stored info')
        self.assertEqual(body['payments'][0]['sport_association'], str(self.sport_association.pk))
        self.assertEqual(body['payments'][0]['amount'], '12.00')
        self.assertEqual(create.call_args.kwargs['amount'], 1200)
        payment.refresh_from_db()
        self.assertEqual(payment.payment_intent_id, 'pi_test')

    def test_ids_as_strings_give_the_same_answer(self):
        payment = self.payment(amount=Decimal('12.00'), paid=False)
        as_object, _ = self.post([{'payment_id': str(payment.payment_id), 'sport_association': 'x'}])
        Payment.objects.filter(pk=payment.pk).update(payment_intent_id=None)
        as_string, _ = self.post([str(payment.payment_id)])
        self.assertEqual(as_object.json()['payments'], as_string.json()['payments'])


class ProfileIntegrationsValidationTests(OwnedFixture):
    def test_valid_values_are_saved(self):
        response = self.client.patch('/profile/integrations', {'review_url': ' https://example.com/r ', 'review_url_enabled': 'on'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'review_url': 'https://example.com/r', 'review_url_enabled': True})
        self.assertEqual(self.client.patch('/profile/integrations', {}, format='json').status_code, 200)

    def test_bad_values_answer_400_and_write_nothing(self):
        self.sport_association.review_url = 'https://before.example'
        self.sport_association.save()
        before = LogEntry.objects.count()
        for body in ({'review_url': 'x' * 256}, {'review_url': ['a']}, {'review_url': {'a': 1}},
                     {'review_url_enabled': 'maybe'}, {'review_url_enabled': None},
                     {'review_url': 'https://after.example', 'review_url_enabled': 'maybe'}):
            response = self.client.patch('/profile/integrations', body, format='json')
            self.assertEqual(response.status_code, 400, body)
            self.sport_association.refresh_from_db()
            self.assertEqual(self.sport_association.review_url, 'https://before.example')
        self.assertEqual(LogEntry.objects.count(), before)
