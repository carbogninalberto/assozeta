"""Client-chosen list orderings: the model's own columns only."""
from django.core.exceptions import FieldError

from application.models import Associate, Course, Invoice, InvoiceSuppliers, Payment, Subscription, SupplierAndCustomers
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_supplier
from application.utils.api_utils import UNKNOWN_ORDERING, checked_ordering


class CheckedOrderingTests(BaseAPITestCase):
    def test_own_columns_pass_unchanged(self):
        cases = {
            SupplierAndCustomers: ['name', '-name', 'tax_code', 'sport_association', 'sport_association_id', 'pk'],
            Payment: ['paid', '-amount', 'user', 'creation_date', 'payment_date'],
            Invoice: ['number', '-activity_fee', 'creation_date'],
            InvoiceSuppliers: ['expire_date', '-payment_date', 'supplier'],
            Subscription: ['status_flag', '-creation_date', 'user'],
            Associate: ['last_name', '-born_date'],
            Course: ['title', '-start_date'],
        }
        for model, orderings in cases.items():
            for ordering in orderings:
                self.assertEqual(checked_ordering(model.objects.all(), ordering), ordering, (model, ordering))

    def test_random_ordering_keeps_its_meaning(self):
        self.assertEqual(checked_ordering(Payment.objects.all(), '?'), '?')

    def test_annotations_of_the_list_are_accepted(self):
        from django.db.models import Value
        queryset = Associate.objects.annotate(tutors_count=Value(1))
        self.assertEqual(checked_ordering(queryset, '-tutors_count'), '-tutors_count')

    def test_account_paths_and_relation_paths_are_unknown(self):
        paths = [
            'sport_association__user__password', '-sport_association__user__email', 'user__password', 'user__email',
            'sport_association__denomination', 'group__name', 'associate__first_name', 'user_id__password',
            '--name', 'name__lower', 'nope', '',
        ]
        for model in (SupplierAndCustomers, Payment, Invoice, InvoiceSuppliers, Subscription, Associate, Course):
            for ordering in paths:
                self.assertEqual(checked_ordering(model.objects.all(), ordering), UNKNOWN_ORDERING, (model, ordering))

    def test_sensitive_own_columns_are_unknown(self):
        self.assertEqual(checked_ordering(Payment.objects.all(), 'payment_intent_id'), UNKNOWN_ORDERING)
        self.assertEqual(checked_ordering(Subscription.objects.all(), '-signature_storage_key'), UNKNOWN_ORDERING)
        self.assertEqual(checked_ordering(Associate.objects.all(), 'id_number'), UNKNOWN_ORDERING)


class SupplierListOrderingTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        create_test_supplier(sport_association=self.sport_association, name='B')
        create_test_supplier(sport_association=self.sport_association, name='A')

    def names(self, **query):
        params = {'sort[field]': 'name', **query}
        reply = self.client.get('/supplier/list', params)
        self.assertEqual(reply.status_code, 200)
        return [row['name'] for row in reply.json()['data']]

    def test_own_column(self):
        self.assertEqual(self.names(), ['A', 'B'])
        self.assertEqual(self.names(**{'sort[sort]': 'desc'}), ['B', 'A'])

    def test_path_through_the_owner_account_answers_as_an_unknown_ordering(self):
        for field in ('sport_association__user__password', 'sport_association__user__email', 'sport_association__user'):
            with self.assertRaises(FieldError):
                self.client.get('/supplier/list', {'sort[field]': field})
        with self.assertRaises(FieldError):
            self.client.get('/supplier/list', {'sort[field]': 'nope'})
