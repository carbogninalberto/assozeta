"""Actual local API/ORM contracts; no Google, email or browser execution.

Only the disposable-database guard is patched for transaction-owned test data.
These tests do not establish capture evidence or external delivery.
"""
import json
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import fixture_id
from application.models import (AttendanceRegistry, CampsAndRetreats, CampsAndRetreatsPeriod,
    CampsAndRetreatsPeriodsService, CampsAndRetreatsSubscription, CampsAndRetreatsSubscriptionPeriod,
    Course, GlobalCalendarEvents, Payment, Reminders, SportAssociation, Subscription, User)
from application.tests.base import BaseTestCase
from application.tests.fixtures.factories import (create_test_payment, create_test_sport_association,
                                                  create_test_subscription)


def event(title='Riunione organizzativa Aurora'):
    return {'event_id': str(uuid4()), 'title': title, 'start': '2026-09-30T08:00:00Z',
            'end': '2026-09-30T09:00:00Z', 'allDay': False, 'className': 'ec-event-solid-primary',
            'extendedProps': {'course': None, 'description': 'Programma del ritiro.', 'instructor': [],
                              'reminder_enabled': False, 'reminder_amount': None, 'reminder_unit': None}}


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ManualCampsCalendarTests(BaseTestCase):
    def seed(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010',
                         reference_date='2026-09-30', verbosity=0)
            return json.loads(output.read_text())

    def setUp(self):
        super().setUp()
        cache.clear()
        self.fixture = self.seed()
        self.assertEqual(self.fixture['fixture_version'], 8)
        self.assertEqual(self.fixture['fixture_profile'], 'baseline')
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.association = SportAssociation.objects.get(pk=fixture_id('association'))
        self.owner_client = self.client_for(self.owner)
        self.reader_client = self.client_for(self.reader)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def create_camp_period(self):
        response = self.owner_client.post('/camps-and-retreats/add', {
            'title': 'Ritiro Aurora 2026', 'description': 'Due giornate di allenamento.'}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        camp = CampsAndRetreats.objects.get(pk=response.data['camp_and_retreat']['camps_and_retreats_id'])
        response = self.owner_client.post('/camps-and-retreats/periods/add', {
            'camps_and_retreats': str(camp.pk), 'title': 'Due giornate Aurora', 'description': 'Allenamento.',
            'start_date': '2026-09-28T00:00:00', 'end_date': '2026-09-29T00:00:00',
            'fee': 80, 'max_participants': 20}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return camp, CampsAndRetreatsPeriod.objects.get(pk=response.data['period']['camps_and_retreats_period_id'])

    def test_camp_period_and_service_actual_writes_persist_without_enrollment_or_payment(self):
        payments = list(Payment._base_manager.values())
        camp, period = self.create_camp_period()
        self.assertEqual(camp.sport_association_id, self.association.pk)
        response = self.owner_client.patch(f'/camps-and-retreats/periods/{period.pk}/update', {
            'title': 'Due giornate aggiornate', 'start_date': '2026-09-29T00:00:00',
            'end_date': '2026-09-30T00:00:00', 'fee': 90, 'max_participants': ''}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        period.refresh_from_db()
        self.assertEqual(period.start_date, datetime(2026, 9, 29, tzinfo=timezone.utc))
        self.assertEqual(period.end_date, datetime(2026, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(period.fee, Decimal('90.00'))
        self.assertIsNone(period.max_participants)
        response = self.client_for(User.objects.get(pk=self.owner.pk)).get(f'/camps-and-retreats/periods/{period.pk}/info')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['data']['start_date'], '29/09/2026')
        self.assertEqual(response.data['data']['end_date'], '30/09/2026')
        response = self.owner_client.post('/camps-and-retreats/periods/services/add', {
            'camps_and_retreats_period': str(period.pk), 'title': 'Pranzo', 'description': 'Pranzo dopo allenamento.',
            'fee': 15, 'payment_category': self.fixture['payment_category_id']}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        service = CampsAndRetreatsPeriodsService.objects.get(pk=response.data['service']['camps_and_retreats_period_service_id'])
        response = self.owner_client.patch(f'/camps-and-retreats/periods/services/{service.pk}/update',
            {'title': 'Pranzo completo', 'fee': 18}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        service.refresh_from_db()
        self.assertEqual(service.title, 'Pranzo completo')
        self.assertEqual(service.fee, Decimal('18.00'))
        self.assertEqual(str(service.payment_category_id), self.fixture['payment_category_id'])
        response = self.owner_client.delete(f'/camps-and-retreats/periods/services/{service.pk}/delete')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(CampsAndRetreatsPeriodsService.objects.filter(pk=service.pk).exists())
        self.assertEqual(self.owner_client.get(f'/camps-and-retreats/periods/{period.pk}/info').data['data']['services'], [])
        self.assertFalse(CampsAndRetreatsSubscription.objects.filter(camps_and_retreats=camp).exists())
        self.assertEqual(list(Payment._base_manager.values()), payments)

    def test_reader_can_consult_and_denied_mutations_leave_camp_and_calendar_unchanged(self):
        camp, period = self.create_camp_period()
        permissions = set(self.reader.collaborator_permissions)
        self.assertTrue({'association.campsandretreats.read', 'association.calendar.read', 'association.events.read'} <= permissions)
        self.assertFalse({'association.campsandretreats.update', 'association.events.update'} & permissions)
        self.assertEqual(self.reader_client.get('/camps-and-retreats/list').status_code, 200)
        self.assertEqual(self.reader_client.get(f'/camps-and-retreats/periods/{period.pk}/info').status_code, 200)
        before = CampsAndRetreatsPeriod.objects.values().get(pk=period.pk)
        for method, url, data in [('post', '/camps-and-retreats/add', {'title': 'Denied'}),
                                 ('patch', f'/camps-and-retreats/periods/{period.pk}/update', {'title': 'Denied'}),
                                 ('delete', f'/camps-and-retreats/{camp.pk}/delete', {})]:
            response = getattr(self.reader_client, method)(url, data, format='json')
            self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(CampsAndRetreatsPeriod.objects.values().get(pk=period.pk), before)
        saved = event()
        response = self.owner_client.post('/calendar/events/update', {'action': 'create', 'events': [saved]}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.reader_client.get('/calendar/events').data['data']['events'][0]['title'], saved['title'])
        for action in ('create', 'update', 'delete'):
            response = self.reader_client.post('/calendar/events/update',
                {'action': action, 'event_id': saved['event_id'], 'events': [dict(saved, title='Denied')]}, format='json')
            self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(GlobalCalendarEvents.objects.get(sport_association=self.association).events, [saved])

    def test_global_event_mutations_preserve_course_lesson_and_ics_excludes_global_events(self):
        global_event = event()
        response = self.owner_client.post('/calendar/events/update', {'action': 'create', 'events': [global_event]}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        changed = deepcopy(global_event)
        changed['title'] = 'Riunione Aurora aggiornata'
        response = self.owner_client.post('/calendar/events/update', {'action': 'update', 'events': [changed]}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        calendar = GlobalCalendarEvents.objects.get(sport_association=self.association)
        self.assertEqual(calendar.events, [changed])
        invalid = dict(changed, end='2026-09-29T00:00:00Z')
        response = self.owner_client.post('/calendar/events/update', {'action': 'update', 'events': [invalid]}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        calendar.refresh_from_db()
        self.assertEqual(calendar.events, [changed])
        lesson = event('Lezione Aurora dal calendario')
        course = Course.objects.get(pk=fixture_id('course'))
        lesson['extendedProps']['course'] = str(course.pk)
        response = self.owner_client.post(f'/course/{course.pk}/calendar/update', {'events': [lesson], 'status': 2}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        registry = AttendanceRegistry.objects.get(course=course)
        self.assertEqual(registry.status, AttendanceRegistry.PUBLISHED)
        self.assertEqual(registry.events[0]['title'], lesson['title'])
        anonymous = APIClient().get(f'/course/{course.pk}/calendar')
        self.assertEqual(anonymous.status_code, 200, anonymous.content)
        self.assertEqual(anonymous.data['data']['events'][0]['title'], lesson['title'])
        exported = self.owner_client.get('/calendar/events/export')
        self.assertEqual(exported.status_code, 200, exported.content)
        text = exported.content.decode()
        self.assertIn('BEGIN:VCALENDAR', text)
        self.assertIn(lesson['title'], text)
        self.assertNotIn(changed['title'], text)
        response = self.owner_client.post('/calendar/events/update', {'action': 'delete', 'event_id': changed['event_id'], 'events': []}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        calendar.refresh_from_db()
        self.assertEqual(calendar.events, [])
        registry.refresh_from_db()
        self.assertEqual(registry.events[0]['title'], lesson['title'])
        self.assertFalse(Reminders.objects.filter(sport_association=self.association).exists())

    def test_fixture_reset_cascades_owned_camps_and_preserves_foreign_camp_calendar_and_payments(self):
        own_camp, own_period = self.create_camp_period()
        foreign = create_test_sport_association()
        foreign_subscription = create_test_subscription(sport_association=foreign)
        foreign_camp = CampsAndRetreats.objects.create(sport_association=foreign, title='Foreign camp', description='Keep')
        foreign_period = CampsAndRetreatsPeriod.objects.create(camps_and_retreats=foreign_camp,
            start_date=datetime(2026, 9, 28, tzinfo=timezone.utc), end_date=datetime(2026, 9, 29, tzinfo=timezone.utc), fee=20)
        children = []
        for camp, period, subscription in [(own_camp, own_period, Subscription.objects.get(pk=fixture_id('subscription-1'))),
                                           (foreign_camp, foreign_period, foreign_subscription)]:
            service = CampsAndRetreatsPeriodsService.objects.create(camps_and_retreats_period=period, title='Keep service', fee=5)
            enrollment = CampsAndRetreatsSubscription.objects.create(camps_and_retreats=camp, subscription=subscription)
            payment = create_test_payment(sport_association=camp.sport_association, associate=subscription.associate)
            joined = CampsAndRetreatsSubscriptionPeriod.objects.create(camps_and_retreats_period=period,
                camps_and_retreats_subscription=enrollment, payment=payment)
            joined.camps_and_retreats_period_services.add(service)
            children.append((service.pk, enrollment.pk, joined.pk, payment.pk))
        own_calendar = GlobalCalendarEvents.objects.create(sport_association=self.association, events=[event('Owned event')])
        foreign_calendar = GlobalCalendarEvents.objects.create(sport_association=foreign, events=[event('Foreign event')])
        foreign_state = {'camp': CampsAndRetreats.objects.values().get(pk=foreign_camp.pk),
                         'period': CampsAndRetreatsPeriod.objects.values().get(pk=foreign_period.pk),
                         'calendar': GlobalCalendarEvents.objects.values().get(pk=foreign_calendar.pk),
                         'payment': Payment._base_manager.values().get(pk=children[1][3])}
        self.seed()
        for model, pk in [(CampsAndRetreats, own_camp.pk), (CampsAndRetreatsPeriod, own_period.pk),
                          (CampsAndRetreatsPeriodsService, children[0][0]), (CampsAndRetreatsSubscription, children[0][1]),
                          (CampsAndRetreatsSubscriptionPeriod, children[0][2]), (GlobalCalendarEvents, own_calendar.pk)]:
            self.assertFalse(model.objects.filter(pk=pk).exists(), model.__name__)
        self.assertFalse(Payment._base_manager.filter(pk=children[0][3]).exists())
        self.assertEqual(CampsAndRetreats.objects.values().get(pk=foreign_camp.pk), foreign_state['camp'])
        self.assertEqual(CampsAndRetreatsPeriod.objects.values().get(pk=foreign_period.pk), foreign_state['period'])
        self.assertEqual(GlobalCalendarEvents.objects.values().get(pk=foreign_calendar.pk), foreign_state['calendar'])
        self.assertEqual(Payment._base_manager.values().get(pk=children[1][3]), foreign_state['payment'])
        self.assertTrue(CampsAndRetreatsPeriodsService.objects.filter(pk=children[1][0]).exists())
        self.assertTrue(CampsAndRetreatsSubscription.objects.filter(pk=children[1][1]).exists())
        self.assertEqual(list(CampsAndRetreatsSubscriptionPeriod.objects.get(pk=children[1][2]).camps_and_retreats_period_services.values_list('pk', flat=True)), [children[1][0]])
