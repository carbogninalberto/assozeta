from unittest.mock import patch

from django.http import HttpResponse

from application.models.courses_models import CourseSubscription
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_course,
    create_test_invoice,
    create_test_payment,
    create_test_subscription,
)


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
