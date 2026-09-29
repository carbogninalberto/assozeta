"""Customer payments may retain a subscription while having no associate."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.http import HttpResponse
from django.test import SimpleTestCase, TransactionTestCase
from django.db import transaction, connection
from django.utils import timezone
from django.utils.html import escape
from rest_framework.test import APIClient
from rest_framework.test import APIRequestFactory, force_authenticate

from application.models.payment_models import Payment, PaymentCategory, SupplierAndCustomers
from application.models.user_models import Instructor, SportAssociation
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_subscription, create_test_associate, create_test_user,
    create_test_sport_association, create_test_instance_config, create_test_billing_subscription,
    create_test_course, create_test_course_subscription,
)
from application.models.invoices_models import Invoice
from application.models.subscriptions_models import SubscriptionMembership
from application.models.user_models import User
from application.utils.payments_utils import generate_invoice_description
from docmanager.views.printing_views import document_invoice


class RecipientDescriptionTests(SimpleTestCase):
    def test_customer_description_is_neutral_and_escaped(self):
        payment = Payment(subject=Payment.SUBSCRIPTION, description='Quota <speciale>',
                          supplier=SupplierAndCustomers(name='Example & Co'))
        description = generate_invoice_description(payment, SportAssociation(denomination='Test ASD'))
        self.assertIn('Example &amp; Co', description)
        self.assertIn('Quota &lt;speciale&gt;', description)
        self.assertNotIn("per l'iscrizione", description)

    def test_instructor_and_category_fallback(self):
        payment = Payment(subject=Payment.COURSE, instructor=Instructor(first_name='Ada', last_name='Rossi'),
                          payment_category=PaymentCategory(name='Attività'))
        description = generate_invoice_description(payment, SportAssociation(denomination='Test ASD'))
        self.assertIn('Ada Rossi', description)
        self.assertIn('Attività', description)

    def test_missing_identity_and_description_have_neutral_fallback(self):
        description = generate_invoice_description(Payment(subject=Payment.SUBSCRIPTION),
                                                  SportAssociation(denomination='Test ASD'))
        self.assertIn('Pagamento', description)
        self.assertNotIn('None', description)


class CustomerPaymentReceiptTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.customer = SupplierAndCustomers.objects.create(
            sport_association=self.sport_association, name='Example Customer', type='customer', email='customer@example.test')
        self.payment = Payment.objects.create(
            user=self.user, sport_association=self.sport_association,
            supplier=self.customer, associate=None, subject=Payment.SUBSCRIPTION,
            amount=Decimal('20.00'), paid=False, expense=False, description='Quota annuale',
            meta={'subscription_data': {'name': 'Annual plan', 'subscription_fee': 20}},
        )
        self.subscription = create_test_subscription(
            sport_association=self.sport_association, user=self.user, payment=self.payment)
        self.print_task = self.enterContext(patch('application.views.payment_views.print_document_invoice.apply_async'))
        self.enterContext(patch('application.views.payment_views.NotificationService.send_notification'))

    def approve(self, **data):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f'/payment/{self.payment.pk}/approve', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.payment.refresh_from_db()
        return response

    def test_approve_customer_with_retained_subscription_and_retry(self):
        self.approve(payment_date='2026-09-29')
        self.assertEqual(timezone.localdate(self.payment.payment_date).isoformat(), '2026-09-29')
        self.assertTrue(self.payment.paid)
        self.assertIsNotNone(self.payment.payment_date)
        self.assertIsNone(self.payment.associate_id)
        self.assertEqual(self.payment.supplier_id, self.customer.pk)
        self.assertEqual(self.payment.user_id, self.user.pk)
        self.assertEqual(self.payment.sport_association_id, self.sport_association.pk)
        self.assertIsNone(self.payment.instructor_id)
        self.assertEqual(self.payment.amount, Decimal('20.00'))
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.payment_id, self.payment.pk)
        self.assertIsNotNone(self.subscription.associate_id)
        invoice = self.payment.invoice
        self.assertIn(self.customer.name, invoice.description)
        self.assertIn('Quota annuale', invoice.description)
        self.assertNotIn("per l'iscrizione", invoice.description)
        self.assertEqual(invoice.membership_fee, Decimal('20.00'))
        self.assertEqual(invoice.activity_fee, Decimal('0.00'))
        self.print_task.assert_called_once()
        self.approve(payment_date='2026-10-01')
        self.assertEqual(self.payment.invoice_id, invoice.pk)
        self.assertEqual(Invoice.objects.filter(sport_association=self.sport_association).count(), 1)
        self.assertEqual(timezone.localdate(self.payment.payment_date).isoformat(), '2026-09-29')

    def test_approve_without_pdf_still_creates_receipt(self):
        self.approve(generate_invoice=False)
        self.assertTrue(self.payment.paid)
        self.assertIsNotNone(self.payment.invoice_id)
        self.print_task.assert_not_called()

    def test_associate_membership_description_is_preserved_with_empty_meta(self):
        self.payment.associate = self.subscription.associate
        self.payment.supplier = None
        for meta in (None, {}, {'subscription_data': None}, {'subscription_data': {}},
                     {'subscription_data': {'name': None}}):
            with self.subTest(meta=meta):
                self.payment.meta = meta
                description = generate_invoice_description(self.payment, self.sport_association)
                self.assertIn(self.subscription.associate.get_full_name(), description)
                self.assertIn("per l'iscrizione", description)
                self.assertNotIn('None', description)

    def test_customer_receipt_templates_render(self):
        # Retained subscription data must not override the actual customer.
        self.subscription.custom_data = {'is_multiple': True, 'denomination': 'Old payer'}
        self.subscription.save(update_fields=['custom_data'])
        self.approve()
        self.payment.invoice.selected_tutor = self.subscription.associate
        self.payment.invoice.save(update_fields=['selected_tutor'])
        for template in ('invoice.html', 'invoice_classic.html'):
            with self.subTest(template=template):
                self.sport_association.invoice_template = template
                self.sport_association.save(update_fields=['invoice_template'])
                response = self.client.get(f'/document/invoice/{self.payment.invoice_id}/view/')
                self.assertEqual(response.status_code, 200)
                self.assertIn(self.customer.name.upper(), response.content.decode())
                self.assertNotIn('OLD PAYER', response.content.decode())

    def test_pdf_success_without_associate_does_not_fail_in_email_composition(self):
        self.approve()
        for send_email in ('false', 'true'):
            with self.subTest(send_email=send_email), patch(
                'docmanager.views.printing_views.PrintingService.print_and_store_pdf',
                return_value=HttpResponse('pdf stored'),
            ), patch('docmanager.views.printing_views.send_mail_async.apply_async') as email_task:
                request = APIRequestFactory().get('/document/invoice/', {'send_receipt_email': send_email})
                force_authenticate(request, self.user)
                response = document_invoice(request, str(self.payment.invoice_id))
                self.assertEqual(response.status_code, 200)
                self.payment.invoice.refresh_from_db()
                self.assertIsNotNone(self.payment.invoice.document_pdf_id)
                email_task.assert_not_called()

    def test_failed_pdf_and_approval_retry_keep_the_paid_receipt(self):
        self.approve()
        invoice_id = self.payment.invoice_id
        with patch('docmanager.views.printing_views.PrintingService.print_and_store_pdf',
                   return_value=HttpResponse('renderer unavailable', status=503)), patch(
                       'docmanager.views.printing_views.send_mail_async.apply_async') as email_task:
            request = APIRequestFactory().get('/document/invoice/', {'send_receipt_email': 'true'})
            force_authenticate(request, self.user)
            response = document_invoice(request, str(invoice_id))
            self.assertEqual(response.status_code, 503)
            email_task.assert_not_called()
        self.approve()
        self.assertTrue(self.payment.paid)
        self.assertEqual(self.payment.invoice_id, invoice_id)
        self.assertIsNone(self.payment.invoice.document_pdf_id)
        self.assertEqual(Invoice.objects.filter(sport_association=self.sport_association).count(), 1)

    def test_associate_receipt_email_remains_opt_in(self):
        associate = self.subscription.associate
        associate.email = 'member@example.test'
        associate.save(update_fields=['email'])
        self.payment.associate = associate
        self.payment.supplier = None
        self.payment.save()
        self.approve()
        for send_email in ('false', 'true'):
            with self.subTest(send_email=send_email), patch(
                'docmanager.views.printing_views.PrintingService.print_and_store_pdf',
                return_value=HttpResponse('pdf stored'),
            ), patch('docmanager.views.printing_views.send_mail_async.apply_async') as email_task:
                request = APIRequestFactory().get('/document/invoice/', {'send_receipt_email': send_email})
                force_authenticate(request, self.user)
                response = document_invoice(request, str(self.payment.invoice_id))
                self.assertEqual(response.status_code, 200)
                if send_email == 'true':
                    email_task.assert_called_once()
                    self.payment.invoice.refresh_from_db()
                    payload = email_task.call_args.kwargs['kwargs']
                    self.assertEqual(payload['recipient_list'], [associate.email])
                    self.assertIn(f'Gentile {associate.get_full_name()}', payload['message'])
                    self.assertIn(str(self.payment.invoice.document_pdf_id), payload['message'])
                else:
                    email_task.assert_not_called()

    def test_fee_allocation_and_relationships_survive_approval(self):
        category = PaymentCategory.objects.create(name='Attività', sport_association=self.sport_association)
        allocation = [{'payment_category_id': str(category.pk), 'amount': 5.25}]
        self.payment.meta_payment_categories = allocation
        self.payment.save()
        original_meta = self.payment.meta
        self.approve(generate_invoice=False)
        self.assertEqual(self.payment.amount, Decimal('20.00'))
        self.assertEqual(self.payment.invoice.membership_fee, Decimal('14.75'))
        self.assertEqual(self.payment.invoice.activity_fee, Decimal('5.25'))
        self.assertEqual(self.payment.invoice.meta_payment_categories, allocation)
        self.assertEqual(self.payment.meta_payment_categories, allocation)
        self.assertEqual(self.payment.meta, original_meta)
        self.assertEqual(self.payment.get_subscription.pk, self.subscription.pk)

    def test_approval_rolls_back_receipt_when_payment_save_fails(self):
        with patch('application.views.payment_views.Payment.save', side_effect=RuntimeError('save failed')):
            with self.assertRaisesMessage(RuntimeError, 'save failed'):
                self.approve()
        self.payment.refresh_from_db()
        self.assertFalse(self.payment.paid)
        self.assertIsNone(self.payment.payment_date)
        self.assertIsNone(self.payment.invoice_id)
        self.assertFalse(Invoice.objects.filter(sport_association=self.sport_association).exists())
        self.print_task.assert_not_called()

    def test_html_is_escaped_in_receipt_and_both_templates(self):
        self.customer.name = 'A & <Co>'
        self.customer.save()
        self.payment.description = 'Quota <b>extra</b> & servizi'
        self.payment.save()
        self.approve(generate_invoice=False)
        self.assertIn(str(escape(self.customer.name)), self.payment.invoice.description)
        self.assertIn(str(escape(self.payment.description)), self.payment.invoice.description)
        for subject in (Payment.SUBSCRIPTION, Payment.OTHER):
            self.payment.subject = subject
            self.payment.save()
            for template in ('invoice.html', 'invoice_classic.html'):
                with self.subTest(subject=subject, template=template):
                    self.sport_association.invoice_template = template
                    self.sport_association.save(update_fields=['invoice_template'])
                    response = self.client.get(f'/document/invoice/{self.payment.invoice_id}/view/')
                    self.assertEqual(response.status_code, 200)
                    html = response.content.decode()
                    self.assertIn(str(escape(self.customer.name.upper())), html)
                    self.assertIn(str(escape(self.payment.description)), html)
                    self.assertNotIn('<b>extra</b>', html)

    def test_member_and_selected_or_main_tutor_render_in_both_templates(self):
        associate = self.subscription.associate
        tutor = create_test_associate(sport_association=self.sport_association,
                                      first_name='Anna', last_name='Tutrice')
        associate.add_main_tutor(tutor)
        self.payment.associate = associate
        self.payment.supplier = None
        self.payment.save()
        self.approve(generate_invoice=False)
        for selected in (None, tutor):
            self.payment.invoice.selected_tutor = selected
            self.payment.invoice.save(update_fields=['selected_tutor'])
            for template in ('invoice.html', 'invoice_classic.html'):
                with self.subTest(selected=selected, template=template):
                    self.sport_association.invoice_template = template
                    self.sport_association.save(update_fields=['invoice_template'])
                    response = self.client.get(f'/document/invoice/{self.payment.invoice_id}/view/')
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(associate.get_full_name().upper(), response.content.decode())
                    self.assertIn(tutor.get_full_name().upper(), response.content.decode())

    def test_instructor_approval_preserves_existing_no_receipt_policy(self):
        instructor = Instructor.objects.create(first_name='Ada', last_name='Rossi', user=self.user)
        self.payment.instructor = instructor
        self.payment.supplier = None
        self.payment.save()
        self.approve()
        self.assertTrue(self.payment.paid)
        self.assertEqual(self.payment.instructor_id, instructor.pk)
        self.assertIsNone(self.payment.invoice_id)
        self.print_task.assert_not_called()

    def test_approval_rejects_another_association(self):
        other = create_test_user(role=User.ASSOCIATION)
        create_test_sport_association(user=other)
        self.client.force_authenticate(other)
        response = self.client.post(f'/payment/{self.payment.pk}/approve', {}, format='json')
        self.assertEqual(response.status_code, 403)
        self.payment.refresh_from_db()
        self.assertFalse(self.payment.paid)
        self.assertIsNone(self.payment.invoice_id)
        self.print_task.assert_not_called()


    def test_member_plan_and_membership_wording_and_escaping(self):
        self.payment.associate = self.subscription.associate
        self.payment.associate.first_name = 'Ada & Eva'
        self.payment.supplier = None
        self.payment.meta = {'subscription_data': {'name': 'Piano <annuale>'}}
        self.subscription.start_date = date(2026, 1, 1)
        self.subscription.end_date = date(2026, 12, 31)
        self.subscription.save()
        SubscriptionMembership.objects.create(
            subscription=self.subscription, sport_association=self.sport_association,
            membership_type='Ente & soci', start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        description = generate_invoice_description(self.payment, self.sport_association)
        self.assertIn("per l'iscrizione 2026 (Piano &lt;annuale&gt;)", description)
        self.assertIn('e tesseramento, 2026 (Ente &amp; soci)', description)
        self.assertIn('Ada &amp; Eva', description)

    def test_member_course_and_missing_description_fallbacks(self):
        self.payment.associate = self.subscription.associate
        self.payment.supplier = None
        self.payment.subject = Payment.COURSE
        self.payment.description = None
        self.payment.payment_category = None
        self.assertEqual(generate_invoice_description(self.payment, self.sport_association), 'Pagamento')
        self.payment.description = '<servizio> & extra'
        self.assertEqual(generate_invoice_description(self.payment, self.sport_association),
                         '&lt;servizio&gt; &amp; extra')
        course = create_test_course(sport_association=self.sport_association, title='Corso <A> & B')
        create_test_course_subscription(course=course, subscription=self.subscription, payment=self.payment)
        description = generate_invoice_description(self.payment, self.sport_association)
        self.assertIn('per il corso <b>Corso &lt;A&gt; &amp; B</b>', description)
        self.assertIn(self.payment.associate.get_full_name(), description)

    def test_customer_template_with_default_but_no_payment_category(self):
        category = PaymentCategory.objects.create(name='Default', sport_association=self.sport_association)
        self.user.default_payment_category = category
        self.user.save(update_fields=['default_payment_category'])
        self.approve(generate_invoice=False)
        Payment.objects.filter(pk=self.payment.pk).update(payment_category=None)
        response = self.client.get(f'/document/invoice/{self.payment.invoice_id}/view/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.customer.name.upper(), response.content.decode())

    def test_anonymous_approval_is_rejected(self):
        self.client.force_authenticate(None)
        response = self.client.post(f'/payment/{self.payment.pk}/approve', {}, format='json')
        self.assertIn(response.status_code, (401, 403))
        self.payment.refresh_from_db()
        self.assertFalse(self.payment.paid)
        self.assertIsNone(self.payment.invoice_id)
        self.print_task.assert_not_called()


class ApprovalCommitTests(TransactionTestCase):
    """Use actual PostgreSQL commits, including an enclosing caller transaction."""

    def setUp(self):
        self.user = create_test_user(role=User.ASSOCIATION)
        association = create_test_sport_association(user=self.user)
        create_test_instance_config(primary_association=association)
        create_test_billing_subscription(user=self.user, plan_type='pro')
        customer = SupplierAndCustomers.objects.create(name='Commit Customer', sport_association=association)
        self.payment = Payment.objects.create(user=self.user, sport_association=association,
                                              supplier=customer, subject=Payment.SUBSCRIPTION,
                                              description='Quota', amount=Decimal('20.00'))
        create_test_subscription(sport_association=association, payment=self.payment)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.print_task = self.enterContext(patch('application.views.payment_views.print_document_invoice.apply_async'))
        self.enterContext(patch('application.views.payment_views.NotificationService.send_notification'))

    def approve(self):
        response = self.client.post(f'/payment/{self.payment.pk}/approve', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def test_dispatch_observes_committed_paid_payment_and_receipt(self):
        def dispatch(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            persisted = Payment.objects.get(pk=self.payment.pk)
            self.assertTrue(persisted.paid)
            self.assertTrue(Invoice.objects.filter(pk=persisted.invoice_id).exists())
            self.assertEqual(kwargs['args'][0], str(persisted.invoice_id))
        self.print_task.side_effect = dispatch
        with transaction.atomic():
            self.approve()
            self.print_task.assert_not_called()
        self.print_task.assert_called_once()

    def test_outer_rollback_discards_pdf_and_receipt(self):
        with self.assertRaisesMessage(RuntimeError, 'rollback'):
            with transaction.atomic():
                self.approve()
                raise RuntimeError('rollback')
        self.payment.refresh_from_db()
        self.assertFalse(self.payment.paid)
        self.assertIsNone(self.payment.invoice_id)
        self.assertFalse(Invoice.objects.exists())
        self.print_task.assert_not_called()

    def test_dispatch_failure_keeps_paid_receipt_and_retry_reuses_it(self):
        self.print_task.side_effect = RuntimeError('broker unavailable')
        self.approve()
        self.payment.refresh_from_db()
        invoice_id = self.payment.invoice_id
        self.assertTrue(self.payment.paid)
        self.assertIsNotNone(invoice_id)
        self.print_task.side_effect = None
        self.approve()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.invoice_id, invoice_id)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(self.print_task.call_args.kwargs['args'][0], str(invoice_id))
