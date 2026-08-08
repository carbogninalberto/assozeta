import datetime
import re
import uuid
from decimal import Decimal

import pytz
from django.db.models import Model
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from application.models.payment_models import Payment
from application.models.subscriptions_models import MedicalCertificate, Subscription, Tags
from application.models.user_models import User
from application.services.subscription_service import SubscriptionImportService, SubscriptionService, TagService
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_payment,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)
from docmanager.models import Document


class SubscriptionServiceTestCase(TestCase):
    def setUp(self):
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.athlete_user = create_test_user(role=User.ATHLETE)
        self.associate = create_test_associate(
            sport_association=self.sport_association,
            user=self.athlete_user,
        )
        self.payment = create_test_payment(
            user=self.athlete_user,
            associate=self.associate,
            sport_association=self.sport_association,
        )
        self.subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
            user=self.athlete_user,
            payment=self.payment,
        )


class SubscriptionServiceUpdateTests(SubscriptionServiceTestCase):
    def test_updates_supported_fields(self):
        data = {
            'type': Subscription.ASSOCIATE_ONLY,
            'role': Subscription.CONSIGLIERE,
            'status_flag': Subscription.NOT_SIGNED,
            'notes': 'Updated notes',
            'custom_data': {'level': 'gold'},
        }

        result = SubscriptionService.update_subscription(self.subscription, data)

        self.assertEqual(result.type, Subscription.ASSOCIATE_ONLY)
        self.assertEqual(result.role, Subscription.CONSIGLIERE)
        self.assertEqual(result.status_flag, Subscription.NOT_SIGNED)
        self.assertEqual(result.notes, 'Updated notes')
        self.assertEqual(result.custom_data, {'level': 'gold'})

    def test_update_clears_generated_document(self):
        document = Document.objects.create(filename='subscription.pdf')
        self.subscription.document_pdf = document
        self.subscription.save()

        result = SubscriptionService.update_subscription(self.subscription, {'notes': 'Changed'})

        self.assertIsNone(result.document_pdf)


class SubscriptionServiceDeleteTests(SubscriptionServiceTestCase):
    def test_delete_rejects_other_association(self):
        other_association = create_test_sport_association()

        with self.assertRaises(PermissionDenied):
            SubscriptionService.delete_subscription(self.subscription, other_association)

    def test_delete_removes_subscription_and_unpaid_payment(self):
        subscription_id = self.subscription.subscription_id
        payment_id = self.payment.payment_id
        self.payment.paid = False
        self.payment.save()

        SubscriptionService.delete_subscription(self.subscription, self.sport_association)

        self.assertFalse(Subscription.objects.filter(subscription_id=subscription_id).exists())
        self.assertFalse(Payment.objects.filter(payment_id=payment_id).exists())


class SubscriptionServiceFeeTests(SubscriptionServiceTestCase):
    def test_resolves_single_fee(self):
        fee, meta = SubscriptionService.resolve_subscription_fee_and_meta(self.sport_association, {})

        self.assertEqual(fee, Decimal('100.00'))
        self.assertIsNone(meta)

    def test_resolves_selected_fee_plan(self):
        self.sport_association.multiple_subscription_fee = True
        self.sport_association.subscription_fee_plans = [
            {'id': 'basic', 'subscription_fee': '150.00', 'name': 'Basic'},
            {'id': 'premium', 'subscription_fee': '200.00', 'name': 'Premium'},
        ]
        self.sport_association.save()

        fee, meta = SubscriptionService.resolve_subscription_fee_and_meta(
            self.sport_association,
            {'plan_id': 'premium'},
        )

        self.assertEqual(fee, '200.00')
        self.assertEqual(meta['subscription_data']['id'], 'premium')

    def test_unknown_fee_plan_falls_back_to_first(self):
        self.sport_association.multiple_subscription_fee = True
        self.sport_association.subscription_fee_plans = [
            {'id': 'basic', 'subscription_fee': '150.00', 'name': 'Basic'},
        ]
        self.sport_association.save()

        fee, meta = SubscriptionService.resolve_subscription_fee_and_meta(
            self.sport_association,
            {'plan_id': 'missing'},
        )

        self.assertEqual(fee, '150.00')
        self.assertEqual(meta['subscription_data']['id'], 'basic')


class SubscriptionServiceMedicalTests(SubscriptionServiceTestCase):
    def test_latest_medical_returns_none_without_certificates(self):
        self.assertIsNone(SubscriptionService.get_latest_medical_certificate(self.associate))

    def test_latest_medical_returns_newest_expiration(self):
        self.subscription.delete()
        older = MedicalCertificate.objects.create(
            user=self.athlete_user,
            expiration_date=datetime.datetime(2025, 1, 1, tzinfo=pytz.UTC),
        )
        newer = MedicalCertificate.objects.create(
            user=self.athlete_user,
            expiration_date=datetime.datetime(2026, 1, 1, tzinfo=pytz.UTC),
        )
        first = Subscription(
            sport_association=self.sport_association,
            associate=self.associate,
            user=self.athlete_user,
            medical=older,
            payment=self.payment,
            start_date=datetime.date(2023, 1, 1),
            end_date=datetime.date(2023, 12, 31),
        )
        Model.save(first)
        second_payment = create_test_payment(
            user=self.athlete_user,
            associate=self.associate,
            sport_association=self.sport_association,
        )
        second = Subscription(
            sport_association=self.sport_association,
            associate=self.associate,
            user=self.athlete_user,
            medical=newer,
            payment=second_payment,
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 12, 31),
        )
        Model.save(second)

        result = SubscriptionService.get_latest_medical_certificate(self.associate)

        self.assertEqual(result, newer)


class TagServiceTests(SubscriptionServiceTestCase):
    def test_create_tag(self):
        tag = TagService.create_tag('Members', self.sport_association)

        self.assertEqual(tag.tag_name, 'Members')
        self.assertEqual(tag.sport_association, self.sport_association)

    def test_create_tag_rejects_blank_names(self):
        for name in (None, '', '   '):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                TagService.create_tag(name, self.sport_association)

    def test_update_tag(self):
        tag = Tags.objects.create(tag_name='Old', sport_association=self.sport_association)

        result = TagService.update_tag(tag.tag_id, 'New', self.sport_association)

        self.assertEqual(result.tag_name, 'New')

    def test_update_tag_rejects_missing_or_foreign_tag(self):
        foreign_tag = Tags.objects.create(tag_name='Foreign', sport_association=create_test_sport_association())
        for tag_id in (uuid.uuid4(), foreign_tag.tag_id):
            with self.subTest(tag_id=tag_id), self.assertRaises(ValidationError):
                TagService.update_tag(tag_id, 'New', self.sport_association)

    def test_delete_tag(self):
        tag = Tags.objects.create(tag_name='Delete', sport_association=self.sport_association)

        TagService.delete_tag(tag.tag_id, self.sport_association)

        self.assertFalse(Tags.objects.filter(tag_id=tag.tag_id).exists())

    def test_delete_missing_tag_raises(self):
        with self.assertRaises(ValidationError):
            TagService.delete_tag(uuid.uuid4(), self.sport_association)

    def test_assign_and_unassign_tag(self):
        tag = Tags.objects.create(tag_name='Assigned', sport_association=self.sport_association)

        result = TagService.assign_tag_to_subscription(
            tag.tag_id,
            self.subscription.subscription_id,
            self.sport_association,
        )
        self.assertIn(tag, result.tags.all())

        result = TagService.unassign_tag_from_subscription(
            tag.tag_id,
            self.subscription.subscription_id,
            self.sport_association,
        )
        self.assertNotIn(tag, result.tags.all())

    def test_assign_rejects_missing_tag_or_subscription(self):
        tag = Tags.objects.create(tag_name='Valid', sport_association=self.sport_association)
        with self.assertRaises(ValidationError):
            TagService.assign_tag_to_subscription(
                uuid.uuid4(), self.subscription.subscription_id, self.sport_association
            )
        with self.assertRaises(ValidationError):
            TagService.assign_tag_to_subscription(tag.tag_id, uuid.uuid4(), self.sport_association)

    def test_unassign_missing_tag_raises(self):
        with self.assertRaises(ValidationError):
            TagService.unassign_tag_from_subscription(
                uuid.uuid4(), self.subscription.subscription_id, self.sport_association
            )


class SubscriptionImportServiceTests(TestCase):
    def test_auto_map_columns(self):
        column_map = {'nome': None, 'cognome': None, 'codice_fiscale': None}

        SubscriptionImportService.auto_map_columns(['Nome', 'Cognome', 'codice_fiscale'], column_map)

        self.assertEqual(column_map, {
            'nome': 'Nome',
            'cognome': 'Cognome',
            'codice_fiscale': 'codice_fiscale',
        })

    def test_auto_map_leaves_unmatched_columns_empty(self):
        column_map = {'nome': None, 'cognome': None}

        SubscriptionImportService.auto_map_columns(['xyz', '123'], column_map)

        self.assertEqual(column_map, {'nome': None, 'cognome': None})

    def test_process_row_value_normalizes_values(self):
        cases = [
            ('  value  ', 'notes', 'value'),
            ('nan', 'notes', None),
            ('1990-01-15', 'born_date', '15/01/1990'),
            ('2024-06-15', 'membership_start_date', '15/06/2024'),
            (None, 'sex', 'A'),
            ('not-a-date', 'born_date', None),
        ]
        for value, field, expected in cases:
            with self.subTest(value=value, field=field):
                self.assertEqual(SubscriptionImportService.process_row_value(value, field), expected)

    def test_validate_row_data(self):
        tax_code_regex = re.compile(
            r'^[a-zA-Z]{6}[0-9]{2}[abcdehlmprstABCDEHLMPRST][0-9]{2}[a-zA-Z][0-9]{3}[a-zA-Z]$',
            re.IGNORECASE,
        )
        row_data = {
            'associate': {
                'first_name': 'Mario',
                'last_name': 'Rossi',
                'tax_code': 'RSSMRA85M01H501Z',
                'born_date': '01/01/1985',
                'sex': 'M',
                'born_city': 'Roma',
                'address': 'Via Test 1',
                'address_cap': '00100',
                'address_city': 'Roma',
            },
            'associate_tutor': {},
        }

        self.assertTrue(SubscriptionImportService.validate_row_data(row_data, tax_code_regex))
        row_data['associate']['tax_code'] = 'INVALID'
        self.assertFalse(SubscriptionImportService.validate_row_data(row_data, tax_code_regex))

    def test_apply_unmapped_static_values(self):
        row_data = {
            'associate': {'first_name': 'Mario'},
            'associate_tutor': {'first_name': 'Luigi'},
        }

        SubscriptionImportService.apply_unmapped_static_values(row_data, {
            'unmapped_static': {'address_city': 'Roma'},
            'unmapped_static_tutor': {'address_city': 'Milano'},
        })

        self.assertEqual(row_data['associate']['address_city'], 'Roma')
        self.assertEqual(row_data['associate_tutor']['address_city'], 'Milano')


class SubscriptionServiceQuickSubscriptionTests(SubscriptionServiceTestCase):
    def setUp(self):
        super().setUp()
        self.subscription.delete()

    def test_create_quick_subscription(self):
        result = SubscriptionService.create_quick_subscription(
            self.sport_association,
            self.associate,
            {},
            self.athlete_user,
        )

        self.assertEqual(result.associate, self.associate)
        self.assertEqual(result.status_flag, Subscription.NOT_SIGNED)
        self.assertIsNotNone(result.payment)

    def test_create_quick_subscription_with_plan(self):
        self.sport_association.multiple_subscription_fee = True
        self.sport_association.subscription_fee_plans = [
            {'id': 'premium', 'subscription_fee': '200.00', 'name': 'Premium'},
        ]
        self.sport_association.save()

        result = SubscriptionService.create_quick_subscription(
            self.sport_association,
            self.associate,
            {'plan_id': 'premium'},
            self.athlete_user,
        )

        self.assertEqual(result.meta['plan_id'], 'premium')
        result.payment.refresh_from_db()
        self.assertEqual(result.payment.amount, Decimal('200.00'))
