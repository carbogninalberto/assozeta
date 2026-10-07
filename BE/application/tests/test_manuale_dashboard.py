"""Actual dashboard handlers, ORM persistence and identity/permission boundaries.

Only the disposable seed guard is patched for this transaction-owned test DB.
These tests establish API behavior; they do not establish browser evidence.
"""
import json
import tempfile
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from freezegun import freeze_time
from rest_framework.test import APIClient

from application.impersonation import begin, end
from application.management.commands.seed_manuale import fixture_id
from application.models import Payment, SportAssociation, Subscription, User
from application.models.subscriptions_models import MedicalCertificate
from application.tests.base import BaseTestCase


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ManualDashboardTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010',
                         reference_date='2026-09-30', verbosity=0)
            self.fixture = json.loads(output.read_text())
        self.assertEqual(self.fixture['fixture_version'], 8)
        self.assertEqual(self.fixture['fixture_profile'], 'baseline')
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.association = SportAssociation.objects.get(pk=fixture_id('association'))
        self.owner_client = self.client_for(self.owner)
        self.reader_client = self.client_for(self.reader)
        self.base = {'rows': [[{'id': 'associates', 'size': 6}, {'id': 'payments', 'size': 6},
            {'id': 'subscriptions', 'size': 4}, {'id': 'subscriptionstoapprove', 'size': 8},
            {'id': 'bestcourses', 'size': 4}, {'id': 'expiringmedicalcertificates', 'size': 4},
            {'id': 'todaylessons', 'size': 4}, {'id': 'staffboard', 'size': 4}]]}

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def saved_profile(self, client):
        response = client.get('/profile/info')
        self.assertEqual(response.status_code, 200, response.content)
        return response.data['user_data']['dashboard_layout']

    def save_layout(self, client, layout, **headers):
        response = client.post('/statistic/dashboard/layout', {'dashboard_layout': layout}, format='json', **headers)
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def widget(self, name, client=None):
        response = (client or self.owner_client).get('/statistic/dashboard', {'widget': name})
        self.assertEqual(response.status_code, 200, response.content)
        return response.data['data']

    def test_owner_add_resize_remove_reset_persist_without_changing_business_records(self):
        self.assertIsNone(self.saved_profile(self.owner_client))
        original_payments = list(Payment._base_manager.values())
        original_subscriptions = list(Subscription._base_manager.values())
        original_owner = User.objects.values().get(pk=self.owner.pk)
        added = deepcopy(self.base)
        added['rows'][0][0]['size'] = 4
        added['rows'][0].append({'id': 'expiredmedicalcertificates', 'size': 12})
        self.save_layout(self.owner_client, added)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.dashboard_layout, added)
        self.assertEqual(self.saved_profile(self.client_for(User.objects.get(pk=self.owner.pk))), added)
        removed = deepcopy(added)
        removed['rows'][0].pop()
        self.save_layout(self.owner_client, removed)
        self.assertEqual(self.saved_profile(self.owner_client), removed)
        self.save_layout(self.owner_client, self.base)
        self.assertEqual(self.saved_profile(self.owner_client), self.base)
        # JSON null selects the UI fallback; it does not delete any business data.
        self.save_layout(self.owner_client, None)
        self.assertIsNone(self.saved_profile(self.owner_client))
        self.assertEqual(User.objects.values().get(pk=self.owner.pk), original_owner)
        self.assertEqual(list(Payment._base_manager.values()), original_payments)
        self.assertEqual(list(Subscription._base_manager.values()), original_subscriptions)

    def test_dashboard_read_permission_saves_collaborator_layout_without_overwriting_owner(self):
        self.save_layout(self.owner_client, self.base)
        self.reader.collaborator_permissions = ['association.dashboard.read']
        self.reader.save(update_fields=['collaborator_permissions'])
        layout = {'rows': [[{'id': 'payments', 'size': 8}, {'id': 'associates', 'size': 4}]]}
        self.assertIsNone(self.saved_profile(self.reader_client))
        self.save_layout(self.reader_client, layout)
        self.reader.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.reader.dashboard_layout, layout)
        self.assertEqual(self.owner.dashboard_layout, self.base)
        fresh = self.client_for(User.objects.get(pk=self.reader.pk))
        self.assertEqual(self.saved_profile(fresh), layout)
        with freeze_time('2026-09-30 12:00:00+00:00'):
            self.assertEqual(self.widget('subscriptions', fresh)['total_subscriptions'], 3)

    def test_missing_dashboard_permission_denies_statistics_and_layout_preserving_both_accounts(self):
        self.save_layout(self.owner_client, self.base)
        self.reader.collaborator_permissions = ['association.members.read']
        self.reader.save(update_fields=['collaborator_permissions'])
        original_owner = User.objects.values().get(pk=self.owner.pk)
        original_reader = User.objects.values().get(pk=self.reader.pk)
        denied_read = self.reader_client.get('/statistic/dashboard', {'widget': 'payments'})
        denied_write = self.reader_client.post('/statistic/dashboard/layout', {'dashboard_layout': {'rows': []}}, format='json')
        self.assertEqual(denied_read.status_code, 403, denied_read.content)
        self.assertEqual(denied_write.status_code, 403, denied_write.content)
        self.assertEqual(User.objects.values().get(pk=self.owner.pk), original_owner)
        self.assertEqual(User.objects.values().get(pk=self.reader.pk), original_reader)

    def test_impersonated_collaborator_saves_selected_account_layout_not_initiating_owner(self):
        self.save_layout(self.owner_client, self.base)
        identifier, _ = begin(self.owner, self.reader.pk)
        layout = {'rows': [[{'id': 'bestcourses', 'size': 12}]]}
        try:
            self.save_layout(self.owner_client, layout, HTTP_USER_ID=str(self.reader.pk),
                             HTTP_X_IMPERSONATION_ID=identifier)
            self.reader.refresh_from_db()
            self.owner.refresh_from_db()
            self.assertEqual(self.reader.dashboard_layout, layout)
            self.assertEqual(self.owner.dashboard_layout, self.base)
        finally:
            end(self.owner, identifier)

    @freeze_time('2026-09-30 12:00:00+00:00')
    def test_baseline_widget_statistics_use_actual_seed_and_expose_correct_empty_states(self):
        self.assertEqual(self.widget('associates')['total_associates'], 3)
        self.assertEqual(self.widget('payments')['total_payments'], 50)
        subscriptions = self.widget('subscriptions')
        self.assertEqual(subscriptions['total_subscriptions'], 3)
        self.assertEqual(sum(subscriptions['pie_subscriptions']), 3)
        best = self.widget('bestcourses')
        self.assertEqual([course['title'] for course in best['best_courses']], ['Ginnastica per tutti'])
        self.assertEqual(best['total_course_associates'], 0)
        for widget, field in [('todaylessons', 'lessons'), ('expiringcarnets', 'expiring_carnets'),
                              ('subscriptionstoapprove', 'subscriptions_to_approve'),
                              ('expiringmedicalcertificates', 'expiring_medical_certificates'),
                              ('expiredmedicalcertificates', 'expired_medical_certificates')]:
            with self.subTest(widget=widget):
                self.assertEqual(self.widget(widget)[field], [])

    @freeze_time('2026-09-30 12:00:00+00:00')
    def test_medical_widgets_distinguish_today_upcoming_thirty_days_and_archived_expired(self):
        subscriptions = [Subscription.objects.get(pk=fixture_id('subscription-' + str(number)))
                         for number in (1, 2, 3)]
        for subscription, days in zip(subscriptions, (0, 30, 31)):
            certificate = MedicalCertificate.objects.create(user=self.owner,
                expiration_date=date(2026, 9, 30) + timedelta(days=days))
            subscription.medical = certificate
            subscription.save(update_fields=['medical'])
        upcoming = self.widget('expiringmedicalcertificates')['expiring_medical_certificates']
        self.assertEqual([row['days_left'] for row in upcoming], [0, 30])
        self.assertEqual(self.widget('expiredmedicalcertificates')['expired_medical_certificates'], [])
        certificate = subscriptions[2].medical
        certificate.expiration_date = date(2026, 9, 29)
        certificate.save(update_fields=['expiration_date'])
        subscriptions[2].archived = True
        subscriptions[2].save(update_fields=['archived'])
        expired = self.widget('expiredmedicalcertificates')['expired_medical_certificates']
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]['days_left'], -1)
        self.assertEqual(str(expired[0]['subscription_id']), str(subscriptions[2].pk))

    @freeze_time('2026-09-30 12:00:00+00:00')
    def test_collected_payments_use_creation_date_while_income_summary_uses_payment_date(self):
        payment = Payment.objects.get(pk=fixture_id('payment-1'))
        payment.creation_date = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        payment.payment_date = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        payment.save(update_fields=['creation_date', 'payment_date'])
        self.assertEqual(self.widget('payments')['total_payments'], 25)
        self.assertEqual(self.widget('incomeAndExpenses')['today_income'], 50)

    @freeze_time('2026-09-30 12:00:00+00:00')
    def test_payments_widget_current_filter_includes_paid_expenses_and_archived_rows(self):
        payment = Payment.objects.get(pk=fixture_id('payment-1'))
        payment.expense = True
        payment.archived = True
        payment.save(update_fields=['expense', 'archived'])
        # This is the inspected behavior behind the manual's indicator warning.
        self.assertEqual(self.widget('payments')['total_payments'], 50)
        income = self.widget('incomeAndExpenses')
        self.assertEqual(income['today_income'], 25)
        self.assertIsNone(income['today_expenses'])
