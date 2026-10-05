"""Real fixture7 enrollment, calendar, carnet and attendance handlers.

These checks exercise the isolated test database. The seed's disposable-instance
guard is the only patched operation; handler success and persistence stay real.
They do not establish browser, scheduled-worker or external delivery evidence.
"""
import json
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from freezegun import freeze_time
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import fixture_id
from application.models import User, Payment
from application.models.attendee_models import AttendanceRegistry, AttendanceDay
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.courses_models import Course, CourseSubscription
from application.tests.base import BaseTestCase


class ManualAttendanceCarnetTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        clock = freeze_time('2026-09-30T12:00:00Z')
        clock.start()
        self.addCleanup(clock.stop)
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010',
                         reference_date='2026-09-30', verbosity=0)
            self.fixture = json.loads(output.read_text())
        self.assertEqual(self.fixture['fixture_version'], 8)
        self.assertEqual(self.fixture['fixture_profile'], 'baseline')
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.course = Course.objects.get(pk=self.fixture['course_id'])
        self.member_id = self.fixture['subscription_ids'][0]
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(CourseSubscription.objects.all_objects().filter(course=self.course).count(), 0)
        self.assertEqual(Carnet.objects.filter(sport_association=self.course.sport_association).count(), 0)
        self.carnet_payload = {'title': 'Carnet ginnastica 5 lezioni',
            'description': 'Pacchetto dimostrativo di cinque lezioni.',
            'fee': '50.00', 'lessons_number': 5, 'subscriptions': []}

    def enroll(self):
        response = self.client.post('/course-subscriptions/add', [{
            'course': str(self.course.pk), 'subscription_id': self.member_id,
            'multiple_quote': None, 'events': [],
        }], format='json')
        self.assertEqual(response.status_code, 201, response.content)
        registration = CourseSubscription.objects.get(course=self.course, subscription_id=self.member_id)
        self.assertEqual(registration.payment.amount, Decimal('120.00'))
        self.assertFalse(registration.payment.paid)
        self.assertEqual(registration.payment.associate_id, registration.subscription.associate_id)
        self.assertEqual(registration.payment.sport_association_id, self.course.sport_association_id)
        return registration

    def create_and_assign_carnet(self):
        before = Payment._base_manager.filter(sport_association=self.course.sport_association).count()
        response = self.client.post('/carnet/add', self.carnet_payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        carnet = Carnet.objects.get(sport_association=self.course.sport_association)
        self.assertTrue(carnet.public)
        self.assertEqual(carnet.lessons_number, 5)
        self.assertEqual(carnet.fee, Decimal('50.00'))
        self.assertEqual(Payment._base_manager.filter(sport_association=self.course.sport_association).count(), before)
        response = self.client.patch(f'/carnet/{carnet.pk}/update', {
            **self.carnet_payload, 'public': False,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        carnet.refresh_from_db()
        self.assertFalse(carnet.public)
        response = self.client.post(f'/carnet/{carnet.pk}/assign/{self.member_id}', {}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        assigned = CarnetSubscription.objects.get(carnet_id=carnet, subscription_id=self.member_id)
        self.assertEqual(assigned.meta, {'lessons_counter': 5, 'lessons_left': 5, 'lessons_registry': []})
        self.assertFalse(assigned.disabled)
        self.assertEqual(assigned.course_subscription.count(), 0)
        self.assertEqual(assigned.payment.amount, Decimal('50.00'))
        self.assertFalse(assigned.payment.paid)
        self.assertEqual(Payment._base_manager.filter(sport_association=self.course.sport_association).count(), before + 1)
        return carnet, assigned

    def link(self, carnet, assigned, registration):
        before = Payment._base_manager.filter(sport_association=self.course.sport_association).count()
        response = self.client.post(f'/carnet/{carnet.pk}/assign/{self.member_id}', {
            'course_id': str(self.course.pk), 'carnet_subscription_id': str(assigned.pk),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(assigned.course_subscription.values_list('pk', flat=True)), [registration.pk])
        self.assertEqual(Payment._base_manager.filter(sport_association=self.course.sport_association).count(), before)

    def publish_lesson(self):
        event = {'event_id': str(fixture_id('attendance-carnet-event')), 'title': 'Lezione dimostrativa di ginnastica',
            'start': '2026-09-30T08:00:00.000Z', 'end': '2026-09-30T09:00:00.000Z',
            'allDay': False, 'extendedProps': {'timeContract': 'utc-v1'}}
        response = self.client.post(f'/course/{self.course.pk}/calendar/update', {
            'events': [event], 'status': AttendanceRegistry.PUBLISHED,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        registry = AttendanceRegistry.objects.get(course=self.course)
        self.assertEqual(registry.status, AttendanceRegistry.PUBLISHED)
        self.assertEqual(registry.events, [event])
        day = AttendanceDay.objects.get(attendance_registry=registry)
        self.assertEqual(str(day.associated_event), event['event_id'])
        self.assertEqual(day.title, event['title'])
        self.assertFalse(day.auto_marked)
        return day

    def mark(self, day, attendees):
        return self.client.post(f'/course/{self.course.pk}/attendees/{day.pk}/update',
            {'attendees': attendees}, format='json')

    def test_real_owner_workflow_persists_single_lesson_consumption_and_idempotent_removal(self):
        registration = self.enroll()
        carnet, assigned = self.create_and_assign_carnet()
        self.link(carnet, assigned, registration)
        response = self.client.post(f'/payment/{assigned.payment_id}/approve', {
            'payment_date': '2026-09-30', 'generate_invoice': False, 'send_receipt_email': False,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        assigned.payment.refresh_from_db()
        self.assertTrue(assigned.payment.paid)
        self.assertIsNotNone(assigned.payment.invoice_id)
        self.assertIsNone(assigned.payment.invoice.document_pdf_id)
        self.assertNotIn('invoice_generating', response.data['data']['payment'])
        day = self.publish_lesson()
        present = [{'course_subscription_id': str(registration.pk)}]
        for _ in range(2):
            marked = self.mark(day, present)
            self.assertEqual(marked.status_code, 200, marked.content)
            assigned.refresh_from_db()
            day.refresh_from_db()
            self.assertEqual(day.attendees, present)
            self.assertEqual(assigned.meta['lessons_left'], 4)
            self.assertEqual(len(assigned.meta['lessons_registry']), 1)
        lesson = assigned.meta['lessons_registry'][0]
        self.assertEqual(lesson['attendance_day_id'], str(day.pk))
        self.assertEqual(lesson['course'], {'id': str(self.course.pk), 'title': self.course.title})
        self.assertEqual(lesson['title'], day.title)
        self.assertEqual(lesson['date'][:10], '2026-09-30')
        fresh = APIClient()
        fresh.force_authenticate(user=User.objects.get(pk=self.owner.pk))
        response = fresh.get(f'/carnet/{carnet.pk}/info')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['data']['subscriptions'][0]['meta']['lessons_left'], 4)
        self.assertEqual(response.data['data']['subscriptions'][0]['payment'], str(assigned.payment_id))
        for _ in range(2):
            removed = self.mark(day, [])
            self.assertEqual(removed.status_code, 200, removed.content)
            assigned.refresh_from_db()
            day.refresh_from_db()
            self.assertEqual(day.attendees, [])
            self.assertEqual(assigned.meta, {'lessons_counter': 5, 'lessons_left': 5, 'lessons_registry': []})
        response = fresh.get(f'/course/{self.course.pk}/attendees')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.data['data']['events']), 1)
        self.assertEqual(response.data['data']['events'][0]['attendees'], [])
        registration.payment.refresh_from_db()
        self.assertFalse(registration.payment.paid)
        self.assertEqual(Payment._base_manager.filter(sport_association=self.course.sport_association).count(), 5)

    def test_unpaid_carnet_refuses_checkin_without_consuming_or_saving_attendance(self):
        registration = self.enroll()
        carnet, assigned = self.create_and_assign_carnet()
        self.link(carnet, assigned, registration)
        day = self.publish_lesson()
        response = self.mark(day, [{'course_subscription_id': str(registration.pk)}])
        self.assertEqual(response.status_code, 412, response.content)
        self.assertEqual(response.data['msg'], 'Il carnet non è ancora stato pagato.')
        assigned.refresh_from_db()
        day.refresh_from_db()
        self.assertEqual(assigned.meta, {'lessons_counter': 5, 'lessons_left': 5, 'lessons_registry': []})
        self.assertFalse(day.attendees)
        self.assertFalse(assigned.payment.paid)

    def test_reader_gets_carnet_registry_and_course_but_six_mutations_preserve_state(self):
        registration = self.enroll()
        carnet, assigned = self.create_and_assign_carnet()
        self.link(carnet, assigned, registration)
        day = self.publish_lesson()
        def snapshot():
            return {
                'carnet': list(Carnet.objects.filter(sport_association=self.course.sport_association).order_by('pk').values()),
                'assigned': list(CarnetSubscription.objects.filter(carnet_id=carnet).order_by('pk').values()),
                'links': list(assigned.course_subscription.values_list('pk', flat=True)),
                'courses': list(CourseSubscription.objects.all_objects().filter(course=self.course).order_by('pk').values()),
                'days': list(AttendanceDay.objects.filter(attendance_registry__course=self.course).order_by('pk').values()),
                'registry': list(AttendanceRegistry.objects.filter(course=self.course).values()),
                'payments': list(Payment._base_manager.filter(sport_association=self.course.sport_association).order_by('pk').values()),
            }
        before = snapshot()
        self.client.force_authenticate(user=self.reader)
        for path in ('/carnet/list', f'/carnet/{carnet.pk}/info',
                     f'/course-subscriptions/list?course_id={self.course.pk}',
                     f'/course/{self.course.pk}/calendar', f'/course/{self.course.pk}/attendees'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.content)
        attempts = [
            ('post', '/course-subscriptions/add', [{'course': str(self.course.pk), 'subscription_id': self.fixture['subscription_ids'][1]}]),
            ('post', '/carnet/add', self.carnet_payload),
            ('patch', f'/carnet/{carnet.pk}/update', {**self.carnet_payload, 'title': 'Negato', 'public': True}),
            ('post', f'/carnet/{carnet.pk}/assign/{self.fixture["subscription_ids"][1]}', {}),
            ('post', f'/course/{self.course.pk}/calendar/update', {'status': 2, 'events': []}),
            ('post', f'/course/{self.course.pk}/attendees/{day.pk}/update', {'attendees': [{'course_subscription_id': str(registration.pk)}]}),
        ]
        for method, path, data in attempts:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, data, format='json')
                self.assertEqual(response.status_code, 403, response.content)
                self.assertEqual(snapshot(), before)

    def test_course_link_requires_existing_registration_and_never_creates_second_payment(self):
        carnet, assigned = self.create_and_assign_carnet()
        before = Payment._base_manager.filter(sport_association=self.course.sport_association).count()
        response = self.client.post(f'/carnet/{carnet.pk}/assign/{self.member_id}', {
            'course_id': str(self.course.pk), 'carnet_subscription_id': str(assigned.pk),
        }, format='json')
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(response.data['msg'], 'Course subscription not found.')
        self.assertEqual(assigned.course_subscription.count(), 0)
        self.assertEqual(Payment._base_manager.filter(sport_association=self.course.sport_association).count(), before)
