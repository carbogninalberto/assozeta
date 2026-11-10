"""Payment.save() takes the default payment category from the payment's association.

The default is the lowest-key category named 'entrate e proventi da attività tipiche'
(case-insensitive) among the payment's own association categories and the global
ones (no association). When none of them has that name the category stays empty.
"""
from uuid import UUID, uuid4

from auditlog.context import disable_auditlog
from django.test import TestCase

from application.models import Payment, PaymentCategory, SportAssociation, User

DEFAULT_NAME = 'Entrate e proventi da attività tipiche'


def key(number):
    return UUID(f'00000000-0000-4000-8000-{number:012d}')


class PaymentDefaultCategoryTests(TestCase):
    def setUp(self):
        with disable_auditlog():
            self.association = SportAssociation.objects.create(user=self.user(), denomination='Own')
            self.other = SportAssociation.objects.create(user=self.user(), denomination='Other')
            # Fixed keys make the order explicit: other < own < global.
            PaymentCategory.objects.filter(name__iexact=DEFAULT_NAME).delete()
            self.other_default = PaymentCategory.objects.create(
                payment_category_id=key(1), name=DEFAULT_NAME, sport_association=self.other)
            self.own_default = PaymentCategory.objects.create(
                payment_category_id=key(2), name='ENTRATE E PROVENTI DA attività TIPICHE', sport_association=self.association)
            self.global_default = PaymentCategory.objects.create(payment_category_id=key(3), name=DEFAULT_NAME)

    @staticmethod
    def user():
        return User.objects.create(username='default-category-' + uuid4().hex, role=User.ASSOCIATION,
                                   password='!test-fixture')

    def saved(self, association, **fields):
        payment = Payment(sport_association=association, amount=10, subject=0, **fields)
        with disable_auditlog():
            payment.save()
        payment.refresh_from_db()
        return payment

    def test_own_category_first(self):
        self.assertEqual(self.saved(self.association).payment_category_id, self.own_default.pk)

    def test_global_category_when_association_has_none(self):
        self.own_default.delete()
        payment = self.saved(self.association)
        self.assertEqual(payment.payment_category_id, self.global_default.pk)
        self.assertEqual(payment.description, DEFAULT_NAME)

    def test_empty_when_only_another_association_has_it(self):
        self.own_default.delete()
        self.global_default.delete()
        payment = self.saved(self.association)
        self.assertIsNone(payment.payment_category_id)
        self.assertIsNone(payment.description)

    def test_payment_without_association_takes_global(self):
        self.assertEqual(self.saved(None).payment_category_id, self.global_default.pk)

    def test_given_category_kept(self):
        chosen = PaymentCategory.objects.create(name='Chosen', sport_association=self.association)
        self.assertEqual(self.saved(self.association, payment_category=chosen).payment_category_id, chosen.pk)

    def test_default_for_takes_instance_or_key(self):
        # The explicit lookups in the subscription, course, camp and payment helpers use default_for.
        self.assertEqual(PaymentCategory.default_for(self.association).pk, self.own_default.pk)
        self.assertEqual(PaymentCategory.default_for(self.association.pk).pk, self.own_default.pk)
        self.assertEqual(PaymentCategory.default_for(self.other).pk, self.other_default.pk)
        self.own_default.delete()
        self.global_default.delete()
        self.assertIsNone(PaymentCategory.default_for(self.association))
