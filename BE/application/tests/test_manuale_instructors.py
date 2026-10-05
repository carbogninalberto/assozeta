"""Real v6 instructor handlers and persistence, independent of browser evidence.

Only the production seed guard is patched inside the transaction-owned test DB.
The UI computes the hourly amount; the API stores it and calculates the summary.
No successful handler or persistence outcome is mocked.
"""
import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import fixture_id
from application.models import Payment, User
from application.models.user_models import Instructor, InstructorHours
from application.tests.base import BaseTestCase
from docmanager.models import Document


class ManualInstructorTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010',
                         reference_date='2026-09-30', verbosity=0)
            self.fixture = json.loads(output.read_text())
        self.assertEqual(self.fixture['fixture_version'], 8)
        self.assertEqual(self.fixture['fixture_profile'], 'baseline')
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.payment_ids = set(Payment._base_manager.values_list('pk', flat=True))
        self.document_ids = set(Document.objects.values_list('pk', flat=True))
        self.payload = {'first_name': 'Paolo', 'last_name': 'Riva', 'email': 'paolo@example.test',
            'born_date': '10/05/1985', 'born_city': 'Roma', 'born_province': 'RM',
            'address_city': 'Roma', 'address': 'Via dello Sport', 'civic_number': '8', 'address_province': 'RM',
            'role': 'Istruttore', 'is_volunteer': False, 'stipulated_contract_in': '30/09/2026',
            'study_title': 'Laurea in scienze motorie', 'associated_user_id': '',
            'default_hourly_billing': '15.00', 'default_percentage_billing': '20.00'}

    def create_instructor(self):
        before = self.client.get('/instructor/list')
        self.assertEqual(before.status_code, 200, before.content)
        self.assertEqual(before.data['data'], [])
        response = self.client.post('/instructor/add', self.payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        saved = Instructor.objects.get(user=self.owner)
        self.assertEqual(saved.first_name, 'Paolo')
        self.assertEqual(saved.last_name, 'Riva')
        self.assertEqual(saved.email, 'paolo@example.test')
        self.assertEqual(saved.user_id, self.owner.pk)
        self.assertEqual(saved.born_date, date(1985, 5, 10))
        self.assertEqual(saved.stipulated_contract_in, date(2026, 9, 30))
        self.assertIsNone(saved.associated_user_id)
        self.assertEqual(saved.default_hourly_billing, Decimal('15.00'))
        self.assertEqual(saved.default_percentage_billing, Decimal('20.00'))
        self.assertEqual(Instructor.objects.count(), 1)
        return saved

    def assert_no_financial_documents(self):
        self.assertEqual(set(Payment._base_manager.values_list('pk', flat=True)), self.payment_ids)
        self.assertEqual(set(Document.objects.values_list('pk', flat=True)), self.document_ids)

    def test_owner_creates_edits_and_records_unpaid_manual_hours_with_real_summary(self):
        instructor = self.create_instructor()
        payload = {**self.payload, 'associated_user_id': None, 'default_hourly_billing': '18.00'}
        edited = self.client.patch(f'/instructor/{instructor.pk}/update', payload, format='json')
        self.assertEqual(edited.status_code, 200, edited.content)
        instructor.refresh_from_db()
        self.assertEqual(instructor.default_hourly_billing, Decimal('18.00'))
        self.assertEqual(instructor.user_id, self.owner.pk)
        list_after_edit = self.client.get('/instructor/list')
        self.assertEqual(list_after_edit.status_code, 200, list_after_edit.content)
        self.assertEqual(len(list_after_edit.data['data']), 1)
        self.assertEqual(Decimal(list_after_edit.data['data'][0]['default_hourly_billing']), Decimal('18.00'))

        # Mirrors the amount computed by AddEditModal's actual hours × rate binding.
        amount = Decimal('3') * instructor.default_hourly_billing
        added = self.client.post(f'/instructor/{instructor.pk}/hours/add', {
            'date': '30/09/2026', 'hours': '3', 'hourly_billing': str(instructor.default_hourly_billing),
            'percentage_billing': '20', 'compensation_type': 'hourly', 'amount': str(amount),
            'paid': False, 'notes': 'Lezione dimostrativa di ginnastica', 'courses': None,
            'calculation_data': [],
        }, format='json')
        self.assertEqual(added.status_code, 200, added.content)
        saved = InstructorHours.objects.get(instructor=instructor)
        self.assertEqual(saved.date, date(2026, 9, 30))
        self.assertEqual(saved.hours, Decimal('3.00'))
        self.assertEqual(saved.hourly_billing, Decimal('18.00'))
        self.assertEqual(saved.amount, Decimal('54.00'))
        self.assertEqual(saved.compensation_type, 'hourly')
        self.assertFalse(saved.paid)
        self.assertIsNone(saved.payment_id)
        self.assertIsNone(saved.document_id)
        self.assertEqual(saved.notes, 'Lezione dimostrativa di ginnastica')

        fresh = APIClient()
        fresh.force_authenticate(user=User.objects.get(pk=self.owner.pk))
        listed = fresh.get(f'/instructor/{instructor.pk}/hours/list')
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed.data['meta']['total'], 1)
        self.assertEqual(Decimal(listed.data['data'][0]['amount']), Decimal('54.00'))
        summary = fresh.get(f'/instructor/{instructor.pk}/info', {'date_range': '01/09/2026 al 30/09/2026'})
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(summary.data['stats'], {'hours': Decimal('3.00'), 'total_amount': Decimal('54.00'),
            'total_amount_paid': Decimal('0'), 'total_amount_to_pay': Decimal('54.00')})
        outside = fresh.get(f'/instructor/{instructor.pk}/info', {'date_range': '01/08/2026 al 31/08/2026'})
        self.assertEqual(outside.status_code, 200, outside.content)
        self.assertEqual(outside.data['stats'], {'hours': 0, 'total_amount': 0,
            'total_amount_paid': 0, 'total_amount_to_pay': 0})
        self.assert_no_financial_documents()

    def test_reader_consults_data_but_three_write_denials_preserve_records(self):
        instructor = self.create_instructor()
        added = self.client.post(f'/instructor/{instructor.pk}/hours/add', {
            'date': '30/09/2026', 'hours': '3', 'hourly_billing': '15', 'amount': '45',
            'compensation_type': 'hourly', 'paid': False, 'notes': 'Lezione dimostrativa',
        }, format='json')
        self.assertEqual(added.status_code, 200, added.content)
        original_instructor = Instructor.objects.values().get(pk=instructor.pk)
        original_hours = list(InstructorHours.objects.filter(instructor=instructor).values())
        self.client.force_authenticate(user=self.reader)
        listed = self.client.get('/instructor/list')
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(len(listed.data['data']), 1)
        hours = self.client.get(f'/instructor/{instructor.pk}/hours/list')
        self.assertEqual(hours.status_code, 200, hours.content)
        self.assertEqual(hours.data['meta']['total'], 1)
        info = self.client.get(f'/instructor/{instructor.pk}/info', {'date_range': '01/09/2026 al 30/09/2026'})
        self.assertEqual(info.status_code, 200, info.content)
        for route, method, payload in [
            ('/instructor/add', self.client.post, {**self.payload, 'first_name': 'Vietato'}),
            (f'/instructor/{instructor.pk}/update', self.client.patch, {**self.payload, 'first_name': 'Vietato'}),
            (f'/instructor/{instructor.pk}/hours/add', self.client.post, {'hours': '1', 'amount': '99'}),
        ]:
            with self.subTest(route=route):
                denied = method(route, payload, format='json')
                self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(Instructor.objects.count(), 1)
        self.assertEqual(Instructor.objects.values().get(pk=instructor.pk), original_instructor)
        self.assertEqual(list(InstructorHours.objects.filter(instructor=instructor).values()), original_hours)
        self.assert_no_financial_documents()
