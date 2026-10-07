from unittest.mock import patch
from datetime import datetime, timezone
from decimal import Decimal
import base64
import tempfile
from io import BytesIO

from bs4 import BeautifulSoup
from PIL import Image
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponse
from django.test import override_settings

from application.models.courses_models import CourseSubscription
from application.models import Invoice, Subscription
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_course,
    create_test_invoice,
    create_test_payment,
    create_test_subscription,
    create_test_sport_association,
)


class DocumentRenderPersistenceTests(BaseAPITestCase):
    def test_subscription_render_completion_keeps_concurrent_card_edit_and_foreign_record(self):
        subscription = create_test_subscription(sport_association=self.sport_association,
            user=self.user, subscription_number='1', subscription_type='Original')
        foreign = create_test_subscription(subscription_number='7', subscription_type='Foreign')
        foreign_before = Subscription._base_manager.filter(pk=foreign.pk).values().get()

        def concurrent_edit(*args, document, **kwargs):
            # The actual renderer endpoint has already fetched the earlier
            # model instance when a user's real PATCH commits during I/O.
            with patch('application.views.subscriptions_views.print_document_subscription.delay'):
                edited = self.client.patch(f'/subscription/{subscription.pk}/update',
                    {'subscription_number': '42', 'subscription_type': 'Tessera Aurora'}, format='json')
            self.assertEqual(edited.status_code, 200, edited.content)
            return HttpResponse(b'%PDF-test', content_type='application/pdf')

        with patch('docmanager.views.printing_views.PrintingService.print_and_store_pdf', side_effect=concurrent_edit):
            response = self.client.get(f'/document/subscription/{subscription.pk}')
        self.assertEqual(response.status_code, 200, response.content)
        subscription.refresh_from_db()
        self.assertEqual(subscription.subscription_number, '42')
        self.assertEqual(subscription.subscription_type, 'Tessera Aurora')
        self.assertIsNotNone(subscription.document_pdf_id)
        self.assertEqual(subscription.sport_association_id, self.sport_association.pk)
        self.assertEqual(Subscription._base_manager.filter(pk=foreign.pk).values().get(), foreign_before)

    def test_invoice_render_completion_keeps_concurrent_business_edit_and_foreign_record(self):
        invoice = create_test_invoice(sport_association=self.sport_association, number=1)
        create_test_payment(sport_association=self.sport_association, invoice=invoice, user=self.user)
        foreign = create_test_invoice(number=7)
        foreign_before = Invoice._base_manager.filter(pk=foreign.pk).values().get()

        def concurrent_edit(*args, document, **kwargs):
            Invoice.objects.filter(pk=invoice.pk).update(number=42, description='Concurrent edit',
                membership_fee=Decimal('25.00'), cancelled=True)
            return HttpResponse(b'%PDF-test', content_type='application/pdf')

        with patch('docmanager.views.printing_views.PrintingService.print_and_store_pdf', side_effect=concurrent_edit):
            response = self.client.get(f'/document/invoice/{invoice.pk}')
        self.assertEqual(response.status_code, 200, response.content)
        invoice.refresh_from_db()
        self.assertEqual(invoice.number, 42)
        self.assertEqual(invoice.description, 'Concurrent edit')
        self.assertEqual(invoice.membership_fee, Decimal('25.00'))
        self.assertTrue(invoice.cancelled)
        self.assertIsNotNone(invoice.document_pdf_id)
        self.assertEqual(invoice.sport_association_id, self.sport_association.pk)
        self.assertEqual(Invoice._base_manager.filter(pk=foreign.pk).values().get(), foreign_before)

    @override_settings(AWS_S3_PUBLIC_BASE_URL='', AWS_S3_USE_OBJECT_ACL=False)
    def test_private_signature_presence_and_real_rendering_keep_actual_png_bytes(self):
        subscription = create_test_subscription(sport_association=self.sport_association,
            user=self.user, status_flag=Subscription.PENDING)
        image = Image.new('RGBA', (4, 3), (255, 255, 255, 0))
        image.putpixel((1, 1), (0, 0, 0, 255))
        image.putpixel((2, 1), (0, 0, 0, 255))
        png = BytesIO(); image.save(png, format='PNG'); contents = png.getvalue()
        data_uri = 'data:image/png;base64,'+base64.b64encode(contents).decode()
        with tempfile.TemporaryDirectory() as temporary, patch(
                'application.models.subscriptions_models.default_storage', FileSystemStorage(location=temporary)):
            subscription.set_signature_from_base64(data_uri)
            subscription.save(update_fields=['signature_url', 'signature_storage_key'])
            subscription.refresh_from_db()
            self.assertIsNone(subscription.signature_url)
            self.assertTrue(subscription.signature_storage_key)
            response = self.client.get('/subscription/list')
            self.assertEqual(response.status_code, 200)
            row = next(row for row in response.data['data'].values()
                if row['subscription_id'] == str(subscription.pk))
            self.assertTrue(row['signature_present'])
            self.assertIsNone(row['signature_url'])
            self.assertNotIn('signature_storage_key', row)
            rendered = self.client.get(f'/document/subscription/{subscription.pk}/view/')
            self.assertEqual(rendered.status_code, 200)
            html = BeautifulSoup(rendered.content, 'html.parser')
            signature = next(img['src'] for img in html.select('img')
                if img.get('src', '').startswith('data:image/png;base64,'))
            self.assertEqual(base64.b64decode(signature.split(',', 1)[1]), contents)


class InvoiceLogoRenderingTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.sport_association.denomination = 'Associazione della ricevuta'
        self.sport_association.document_header = '<p>Intestazione della ricevuta</p>'
        self.sport_association.save(update_fields=['denomination', 'document_header'])
        foreign = create_test_sport_association(denomination='Associazione estranea')
        create_test_invoice(sport_association=foreign, number=999)
        self.invoice = create_test_invoice(sport_association=self.sport_association,
            number=42, membership_fee=Decimal('25.00'), activity_fee=Decimal('50.00'))
        person = create_test_associate(sport_association=self.sport_association,
            first_name='Giulia', last_name='Rossi')
        create_test_payment(user=self.user, associate=person, sport_association=self.sport_association,
            invoice=self.invoice, amount=Decimal('75.00'),
            payment_date=datetime(2026, 9, 30, 12, tzinfo=timezone.utc))

    def rendered(self, template, logo):
        self.sport_association.invoice_template = template
        self.sport_association.logo = logo
        self.sport_association.save(update_fields=['invoice_template', 'logo'])
        response = self.client.get(f'/document/invoice/{self.invoice.pk}/view/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Intestazione della ricevuta', html)
        self.assertIn('RICEVUTA Associazione della ricevuta', html)
        self.assertIn('N° 42 / 2026', html)
        self.assertIn('GIULIA ROSSI', html)
        self.assertIn('€ 75,00', html)
        self.assertIn('30.09.2026', html)
        self.assertNotIn('Associazione estranea', html)
        return BeautifulSoup(html, 'html.parser')

    def test_absent_logo_omits_image_in_both_real_rendered_templates(self):
        for template in ('invoice.html', 'invoice_classic.html'):
            for logo in (None, ''):
                with self.subTest(template=template, logo=logo):
                    html = self.rendered(template, logo)
                    self.assertEqual(html.select('img.logo'), [])
                    self.assertFalse(any(image.get('src') in (None, '', 'None')
                        for image in html.select('img')))

    def test_present_logo_retains_its_actual_source_in_both_real_rendered_templates(self):
        logo = 'https://assets.example.test/association-logo.png'
        for template in ('invoice.html', 'invoice_classic.html'):
            with self.subTest(template=template):
                images = self.rendered(template, logo).select('img.logo')
                self.assertEqual(len(images), 1)
                self.assertEqual(images[0]['src'], logo)


class SubscriptionLogoRenderingTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.sport_association.denomination = 'Associazione del modulo'
        self.sport_association.document_header = '<p>Intestazione del modulo</p>'
        self.sport_association.save(update_fields=['denomination', 'document_header'])
        person = create_test_associate(sport_association=self.sport_association,
            first_name='Giulia', last_name='Rossi', tax_code='RSSGLI95C50H501X')
        self.subscription = create_test_subscription(sport_association=self.sport_association,
            associate=person, user=self.user,
            creation_date=datetime(2026, 9, 30, 12, tzinfo=timezone.utc))
        create_test_sport_association(denomination='Associazione estranea')

    def rendered(self, template, logo):
        self.sport_association.subscription_template = template
        self.sport_association.logo = logo
        self.sport_association.save(update_fields=['subscription_template', 'logo'])
        response = self.client.get(f'/document/subscription/{self.subscription.pk}/view/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Intestazione del modulo', html)
        self.assertIn('Associazione del modulo', html)
        self.assertIn('GIULIA ROSSI', html)
        self.assertIn('RSSGLI95C50H501X', html)
        self.assertIn('30.09.2026', html)
        self.assertIn('socio e tesserato', html)
        self.assertNotIn('Associazione estranea', html)
        return BeautifulSoup(html, 'html.parser')

    def test_absent_logo_omits_image_in_both_real_rendered_templates(self):
        for template in ('subscription.html', 'subscription_classic.html'):
            for logo in (None, ''):
                with self.subTest(template=template, logo=logo):
                    html = self.rendered(template, logo)
                    self.assertEqual(html.select('img.logo'), [])
                    self.assertFalse(any(image.get('src') in (None, '', 'None')
                        for image in html.select('img')))

    def test_present_logo_retains_its_actual_source_in_both_real_rendered_templates(self):
        logo = 'https://assets.example.test/association-logo.png'
        for template in ('subscription.html', 'subscription_classic.html'):
            with self.subTest(template=template):
                images = self.rendered(template, logo).select('img.logo')
                self.assertEqual(len(images), 1)
                self.assertEqual(images[0]['src'], logo)


class InvoiceExtraTextMentionTests(BaseAPITestCase):
    def test_extra_text_mentions_are_resolved_for_every_invoice_template(self):
        associate = self.sport_association.user.associate_set.first()
        if associate is None:
            associate = create_test_associate(sport_association=self.sport_association)
        associate.first_name = 'Giulia'
        associate.last_name = 'Rossi'
        associate.save(update_fields=['first_name', 'last_name'])

        invoice = create_test_invoice(sport_association=self.sport_association, number=42)
        payment = create_test_payment(
            user=self.user,
            associate=associate,
            sport_association=self.sport_association,
            invoice=invoice,
        )
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=associate,
            user=self.user,
            payment=payment,
        )
        course = create_test_course(
            sport_association=self.sport_association,
            title='Pilates serale',
        )
        CourseSubscription.objects.create(course=course, subscription=subscription)
        self.sport_association.extra_text_invoices = (
            '<p>@nome @cognome — @listacorsi — '
            '<span class="mention" data-type="mention" key="invoice.number">@numero</span></p>'
        )

        for template_name in ('invoice.html', 'invoice_classic.html'):
            with self.subTest(template_name=template_name):
                self.sport_association.invoice_template = template_name
                self.sport_association.save(
                    update_fields=['extra_text_invoices', 'invoice_template'],
                )
                with patch(
                    'docmanager.views.printing_views.render',
                    return_value=HttpResponse('ok'),
                ) as render_mock:
                    response = self.client.get(
                        f'/document/invoice/{invoice.invoice_id}/view/',
                    )

                self.assertEqual(response.status_code, 200)
                _, rendered_template, context = render_mock.call_args.args
                self.assertEqual(rendered_template, f'document/application/{template_name}')
                extra_text = context['extra_text_invoices']
                self.assertIn('Giulia Rossi', extra_text)
                self.assertIn('Pilates serale', extra_text)
                self.assertIn('42', extra_text)
                self.assertNotIn('@nome', extra_text)
                self.assertNotIn('@listacorsi', extra_text)

    def test_extra_text_handles_selected_tutor_and_missing_subscription(self):
        associate = create_test_associate(
            sport_association=self.sport_association,
            first_name='Marco',
            last_name='Bianchi',
        )
        tutor = create_test_associate(
            sport_association=self.sport_association,
            first_name='Anna',
            last_name='Verdi',
        )
        invoice = create_test_invoice(
            sport_association=self.sport_association,
            selected_tutor=tutor,
        )
        create_test_payment(
            user=self.user,
            associate=associate,
            sport_association=self.sport_association,
            invoice=invoice,
        )
        self.sport_association.extra_text_invoices = (
            '<p>@nome — @nometutore @cognometutore — corsi: @listacorsi</p>'
        )
        self.sport_association.save(update_fields=['extra_text_invoices'])

        with patch(
            'docmanager.views.printing_views.render',
            return_value=HttpResponse('ok'),
        ) as render_mock:
            response = self.client.get(f'/document/invoice/{invoice.invoice_id}/view/')

        self.assertEqual(response.status_code, 200)
        extra_text = render_mock.call_args.args[2]['extra_text_invoices']
        self.assertIn('Marco — Anna Verdi — corsi:', extra_text)
        self.assertNotIn('@listacorsi', extra_text)
