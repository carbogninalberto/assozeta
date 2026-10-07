"""Prepared real-task/persistence regressions; no scheduled-delivery evidence."""
from datetime import timedelta
from freezegun import freeze_time
from django.utils import timezone
from application.models import User, AttendanceRegistry, AttendanceDay
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.payment_models import Payment
from application.tasks import auto_mark_attendance
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_user, create_test_course, create_test_subscription,
    create_test_associate, create_test_course_subscription,
)


class AutomaticAttendanceResultTests(BaseAPITestCase):
    def setUp(self):
        clock = freeze_time('2026-10-02T13:00:00Z')
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.user.auto_mark_attendance = True
        self.user.save(update_fields=['auto_mark_attendance'])
        self.course = create_test_course(sport_association=self.sport_association)
        self.registry = AttendanceRegistry.objects.create(course=self.course, status=AttendanceRegistry.PUBLISHED, events=[])
        self.day = AttendanceDay.objects.create(attendance_registry=self.registry, title='Lezione automatica',
            date=timezone.now()-timedelta(minutes=30), attendees=[], expected_absences=[], auto_marked=False)
        self.athlete = create_test_user(role=User.ATHLETE)
        self.enrollments = {}
        self.assignments = {}
        cases = {'paid': [(5, False, True)], 'absence': [(5, False, True)], 'unpaid': [(3, False, False)],
                 'exhausted': [(0, False, True)], 'disabled': [(4, True, True)],
                 'multiple': [(2, False, True), (5, False, True)], 'no-carnet': [],
                 'unpaid-priority': [(1, False, False), (5, False, True)]}
        for key, balances in cases.items():
            user = self.athlete if key in ['paid', 'absence'] else self.user
            person = create_test_associate(sport_association=self.sport_association)
            subscription = create_test_subscription(sport_association=self.sport_association, associate=person, user=user)
            enrollment = create_test_course_subscription(course=self.course, subscription=subscription)
            self.enrollments[key] = enrollment
            self.assignments[key] = []
            for remaining, disabled, settle in balances:
                carnet = Carnet.objects.create(user_id=self.user, sport_association=self.sport_association,
                    title='Carnet '+key, lessons_number=5, fee=50)
                payment = Payment.objects.create(user=self.user, sport_association=self.sport_association,
                    associate=person, amount=50, type=Payment.CASH, subject=Payment.COURSE, paid=False, expense=False)
                assignment = CarnetSubscription.objects.create(user_id=self.user, subscription=subscription,
                    carnet_id=carnet, payment=payment, disabled=disabled,
                    meta={'lessons_left': remaining, 'lessons_counter': 5, 'lessons_registry': []})
                assignment.course_subscription.add(enrollment)
                self.assignments[key].append(assignment)
                if settle:
                    response = self.client.post(f'/payment/{payment.pk}/approve',
                        {'generate_invoice': False, 'send_receipt_email': False}, format='json')
                    self.assertEqual(response.status_code, 200)
                    payment.refresh_from_db()
                    self.assertTrue(payment.paid)
        self.client.force_authenticate(user=self.athlete)
        response = self.client.post(f'/attendance-day/{self.day.pk}/mark-absent',
            {'course_subscription_id': str(self.enrollments['absence'].pk), 'absent': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.client.force_authenticate(user=self.user)

    def test_athlete_dashboard_rereads_saved_absence_and_presence(self):
        import uuid
        event_id = str(uuid.uuid4())
        start = timezone.now() + timedelta(minutes=30)
        self.day.date = start
        self.day.associated_event = event_id
        self.day.save()
        self.registry.events = [{'event_id': event_id, 'title': 'Lezione',
            'start': start.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
            'end': (start + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S.000Z')}]
        self.registry.save()
        self.client.force_authenticate(user=self.athlete)
        enrollment = str(self.enrollments['absence'].pk)
        for absent in [True, False, True]:
            response = self.client.post(f'/attendance-day/{self.day.pk}/mark-absent',
                {'course_subscription_id': enrollment, 'absent': absent}, format='json')
            self.assertEqual(response.status_code, 200)
            dashboard = self.client.get('/statistic/athlete-dashboard')
            self.assertEqual(dashboard.status_code, 200)
            lesson = next(row for row in dashboard.data['upcoming_lessons']
                          if str(row['course_subscription_id']) == enrollment)
            self.assertEqual(lesson['is_absent'], absent)

    def snapshot(self):
        self.day.refresh_from_db()
        rows = {}
        for key, assignments in self.assignments.items():
            rows[key] = []
            for assignment in assignments:
                assignment.refresh_from_db()
                rows[key].append(assignment.meta)
        return {'attendees': self.day.attendees, 'absences': self.day.expected_absences,
                'processed': self.day.auto_marked, 'carnets': rows}

    def test_real_task_persists_variants_absence_and_repeat_without_double_consumption(self):
        future = AttendanceDay.objects.create(attendance_registry=self.registry, title='Future',
            date=timezone.now()+timedelta(hours=1), attendees=[], expected_absences=[])
        stale = AttendanceDay.objects.create(attendance_registry=self.registry, title='Old',
            date=timezone.now()-timedelta(hours=25), attendees=[], expected_absences=[])
        auto_mark_attendance.run()
        result = self.snapshot()
        self.assertTrue(result['processed'])
        self.assertEqual(sorted(row['course_subscription_id'] for row in result['attendees']),
            sorted(str(self.enrollments[key].pk) for key in ['paid', 'disabled', 'multiple', 'no-carnet']))
        expected = {'paid': [4], 'absence': [5], 'unpaid': [3], 'exhausted': [0],
                    'disabled': [4], 'multiple': [1, 5], 'no-carnet': [], 'unpaid-priority': [1, 5]}
        for key, balances in expected.items():
            self.assertEqual([row['lessons_left'] for row in result['carnets'][key]], balances)
            for index, meta in enumerate(result['carnets'][key]):
                expected_consumption = key in ['paid', 'multiple'] and index == 0
                self.assertEqual(len(meta['lessons_registry']), int(expected_consumption))
        self.assertEqual(result['absences'], [{'course_subscription_id': str(self.enrollments['absence'].pk)}])
        auto_mark_attendance.run()
        self.assertEqual(self.snapshot(), result)
        for day in (future, stale):
            day.refresh_from_db()
            self.assertFalse(day.auto_marked)
            self.assertEqual(day.attendees, [])

    def test_disabled_owner_option_preserves_pending_date_and_carnets(self):
        before = self.snapshot()
        self.user.auto_mark_attendance = False
        self.user.save(update_fields=['auto_mark_attendance'])
        auto_mark_attendance.run()
        self.assertEqual(self.snapshot(), before)
