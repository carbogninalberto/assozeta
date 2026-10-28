from django.test import SimpleTestCase
from django.template.loader import render_to_string


class ExternalEmailTemplateTests(SimpleTestCase):
    def test_payment_receipt_renders_values_and_approved_description_html(self):
        html = render_to_string('email/payment/payment_receipt.html', {
            'recipient_name': 'Ada <Rossi>',
            'amount': 150.0,
            'receipt_number': 42,
            'payment_date': '22/10/2026',
            'description': 'Ricevuta <b>approvata</b>',
            'download_url': 'https://example.test/receipt?id=42&token=abc',
            'association_name': 'Associazione & Co',
        })

        self.assertIn('Ada &lt;Rossi&gt;', html)
        self.assertIn('150.0€ n. 42 del 22/10/2026', html)
        self.assertIn('Ricevuta <b>approvata</b>', html)
        self.assertIn('Associazione &amp; Co', html)
        self.assertIn("font-family: 'Poppins', sans-serif", html)
        self.assertIn('.email-button', html)
        self.assertIn('background-color: #f8fafc', html)

    def test_pending_payment_escapes_user_values_and_supports_online_payments(self):
        html = render_to_string('email/payment/pending.html', {
            'recipient_name': 'Ada <Rossi>',
            'description': 'per il corso <extra>',
            'amount': '25.00',
            'application_name': 'App & Co',
            'application_url': 'https://example.test/?a=1&b=2',
            'association_name': 'Club <Test>',
            'online_payments_enabled': True,
        })

        self.assertIn('Ada &lt;Rossi&gt;', html)
        self.assertIn('per il corso &lt;extra&gt;', html)
        self.assertIn('App &amp; Co', html)
        self.assertIn('Puoi pagare online!', html)
        self.assertNotIn('<extra>', html)

    def test_event_reminder_escapes_event_content(self):
        html = render_to_string('email/reminders/event.html', {
            'event_title': 'Corso <speciale>',
            'event_reminder_text': '30 minuti',
            'event_description': 'Descrizione & dettagli',
            'association_name': 'Club <Test>',
            'application_name': 'App & Co',
        })

        self.assertIn('Corso &lt;speciale&gt;', html)
        self.assertIn('Descrizione &amp; dettagli', html)
        self.assertNotIn('<speciale>', html)

    def test_assigned_fee_escapes_fee_data(self):
        html = render_to_string('email/payment/fee_assigned.html', {
            'recipient_name': 'Ada <Rossi>',
            'fee_name': 'Quota <annuale>',
            'amount': '80.00',
            'application_name': 'App & Co',
            'association_name': 'Club <Test>',
        })

        self.assertIn('Ada &lt;Rossi&gt;', html)
        self.assertIn('Quota &lt;annuale&gt;', html)
        self.assertIn('80.00 €', html)
        self.assertIn('App &amp; Co', html)
        self.assertNotIn('<annuale>', html)
