"""The family wizard's answer lists the entries' payments as they are stored."""
import copy
import uuid

from django.utils import timezone
from rest_framework.renderers import JSONRenderer

from application.models import Family, Payment
from application.models.subscriptions_models import Subscription
from application.serializers.payment_serializers import PaymentSerializer
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_associate, create_test_payment


def new_tax_code():
    return f'FMGFBE10A01{uuid.uuid4().hex[:4].upper()}G'


def entry(first_name, tax_code=None, **extra):
    return {
        'new_user_account': {'new_member': False},
        'plan_id': None, 'membership_plan_id': None,
        'associate_data': {'type': 1, 'is_minor': False, 'first_name': first_name, 'last_name': 'Famiglia', 'sex': 'M',
                           'tax_code': tax_code or new_tax_code(), 'born_date': '10/02/1990', 'born_city': 'Roma',
                           'address': 'Via Roma 1', 'address_city': 'Roma', 'address_cap': '00100', 'phone': None, 'email': None},
        'associate_tutor_data': None,
        'signature': {'there_is_signature': False, 'data': None},
        'medical_certificate': {'medical_id': None, 'certificate_expring_date': None},
        'custom_data': {}, 'additional_fields': None,
        **extra,
    }


def stored(payment_id):
    """PaymentSerializer of the stored row, as rendered."""
    import json
    return json.loads(JSONRenderer().render(PaymentSerializer(Payment.objects.get(pk=payment_id)).data))


class FamilyWizardResponseTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.sport_association.enable_quotes_management = True
        self.sport_association.multiple_subscription_fee = False
        self.sport_association.save()

    def post(self, entries):
        body = {**entry('Capo'), 'is_family_wizard': True, 'sport_association': self.user.username,
                'multiple_entry_form_data': entries}
        return self.client.post('/subscription/add', copy.deepcopy(body), format='json')

    def test_created_payments_are_serialized_as_stored(self):
        first, second = entry('Febo', valid=True), entry('Fiona', valid=True)
        response = self.post([first, entry('Franco', valid=False), second])

        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        body = json_of(response)
        self.assertEqual(set(body), {'status', 'subs', 'payments'})
        self.assertEqual((body['status'], body['subs']), ('success', []))
        family = Family.objects.get()
        subscriptions = [Subscription.objects.get(associate__tax_code=item['associate_data']['tax_code']) for item in (first, second)]
        self.assertEqual({subscription.associate.family_id for subscription in subscriptions}, {family.family_id})
        self.assertEqual([item['payment_id'] for item in body['payments']],
                         [str(subscription.payment_id) for subscription in subscriptions])
        for item in body['payments']:
            self.assertEqual(item, stored(item['payment_id']))
            # The stored value is a datetime: the first instant of the payment's day.
            self.assertRegex(item['payment_date'], r'^\d{4}-\d{2}-\d{2}T')
            self.assertEqual(item['amount'], '100.00')

    def test_created_and_reused_payments_have_the_same_shape(self):
        tax_code = new_tax_code()
        payer = create_test_associate(sport_association=self.sport_association, user=self.user, first_name='Rocco',
                                      last_name='Famiglia', tax_code=tax_code)
        reusable = create_test_payment(user=self.user, associate=payer, sport_association=self.sport_association, paid=False,
                                       payment_date=timezone.now().date())
        response = self.post([entry('Rocco', tax_code, valid=True), entry('Febo', valid=True)])

        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        reused, created = json_of(response)['payments']
        self.assertEqual(reused['payment_id'], str(reusable.payment_id))
        self.assertNotEqual(created['payment_id'], reused['payment_id'])
        self.assertEqual(set(created), set(reused))
        self.assertEqual(created['payment_date'], reused['payment_date'])
        self.assertEqual(created, stored(created['payment_id']))
        self.assertEqual(reused, stored(reused['payment_id']))


def json_of(response):
    import json
    return json.loads(response.content)
