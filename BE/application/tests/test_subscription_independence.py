"""Software billing must never decide access to association business data."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from application.models import BillingPlan, BillingSubscription, SportAssociation, User
from instance.models import InstanceConfiguration


class SubscriptionIndependentAccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username='owner', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='Test club')
        self.config = InstanceConfiguration.objects.create(
            domain='club.example.test', name='Test club', self_hosted=True,
            primary_association=self.association)
        self.client.force_authenticate(self.owner)

    def test_business_access_does_not_query_software_billing(self):
        with patch.object(BillingSubscription.objects, 'get', side_effect=AssertionError('Billing is not authorization')):
            for path in ('/collaborators/list', '/communications/messages/list', '/communications/workflows/list'):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200, response.content)

    def test_legacy_plan_states_have_identical_access(self):
        for name, plan_type, ends_on, renewal in (
            ('expired', 2, timezone.now() - timedelta(days=10), True),
            ('cancelled', 1, timezone.now() - timedelta(days=10), False),
            ('paid', 3, timezone.now() + timedelta(days=30), True),
            ('no_expiration', 1, None, False),
        ):
            with self.subTest(state=name):
                plan = BillingPlan.objects.create(name=name, billing_type=plan_type)
                sub = BillingSubscription.objects.create(user=self.owner, billing_plan=plan,
                    ends_on=ends_on, auto_renewal=renewal)
                self.assertEqual(self.client.get('/collaborators/list').status_code, 200)
                sub.delete()

    def test_retired_billing_endpoint_does_not_create_an_entitlement(self):
        BillingPlan.objects.create(name='Piano Pro', billing_type=2)
        self.assertEqual(self.client.get('/billing/active-plan').status_code, 410)
        self.assertFalse(BillingSubscription.objects.exists())

    def test_legacy_seed_command_never_creates_or_renews_entitlements(self):
        call_command('seed_selfhost', stdout=StringIO())
        self.assertFalse(BillingPlan.objects.exists())
        self.assertFalse(BillingSubscription.objects.exists())

    def test_restricted_collaborator_and_athlete_remain_denied(self):
        collaborator = User.objects.create_user(username='restricted', role=User.COLLABORATOR,
            connected_user=self.owner, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.calendar.read'])
        athlete = User.objects.create_user(username='athlete', role=User.ATHLETE)
        for user in (collaborator, athlete):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user)
                self.assertEqual(self.client.get('/collaborators/list').status_code, 403)

    def test_full_collaborator_does_not_need_owner_plan(self):
        user = User.objects.create_user(username='staff', role=User.COLLABORATOR,
            connected_user=self.owner, collaborator_role=User.FULL)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get('/collaborators/list').status_code, 200)

    def test_staff_board_socket_access_uses_roles_without_a_plan(self):
        from communications.staff_board import readable_association_id
        reader = User.objects.create_user(username='board-reader', role=User.COLLABORATOR,
            connected_user=self.owner, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.communication.messages.read'])
        self.assertEqual(readable_association_id(self.owner.pk), str(self.association.pk))
        self.assertEqual(readable_association_id(reader.pk), str(self.association.pk))
        reader.collaborator_permissions = []
        reader.save(update_fields=['collaborator_permissions'])
        self.assertIsNone(readable_association_id(reader.pk))

    def test_automatic_attendance_runs_without_a_plan_and_keeps_opt_out(self):
        from freezegun import freeze_time
        from application.tasks import auto_mark_attendance
        from application.models import AttendanceRegistry, AttendanceDay
        from application.tests.fixtures.factories import (
            create_test_course, create_test_subscription, create_test_course_subscription)
        with freeze_time('2026-10-02T13:00:00Z'):
            self.owner.auto_mark_attendance = True
            self.owner.save(update_fields=['auto_mark_attendance'])
            course = create_test_course(sport_association=self.association)
            subscription = create_test_subscription(sport_association=self.association, user=self.owner)
            enrollment = create_test_course_subscription(course=course, subscription=subscription)
            registry = AttendanceRegistry.objects.create(course=course, events=[])
            day = AttendanceDay.objects.create(attendance_registry=registry,
                date=timezone.now() - timedelta(minutes=30), attendees=[], expected_absences=[])
            auto_mark_attendance.run()
            day.refresh_from_db()
            self.assertTrue(day.auto_marked)
            self.assertEqual(day.attendees, [{'course_subscription_id': str(enrollment.pk)}])
            self.owner.auto_mark_attendance = False
            self.owner.save(update_fields=['auto_mark_attendance'])
            later = AttendanceDay.objects.create(attendance_registry=registry,
                date=timezone.now() - timedelta(minutes=10), attendees=[], expected_absences=[])
            auto_mark_attendance.run()
            later.refresh_from_db()
            self.assertFalse(later.auto_marked)
            self.assertEqual(later.attendees, [])

    @override_settings(IS_WHITELABEL=False)
    def test_retired_platform_nurturing_never_queries_or_sends(self):
        from application.tasks import nurturing_warm_leads
        with self.assertNumQueries(0), patch('application.tasks.send_email_template') as send:
            nurturing_warm_leads.run()
        send.assert_not_called()

    def test_data_migration_disables_only_retired_software_jobs(self):
        import importlib
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        interval = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.DAYS)
        retired = ['auto_move_users_to_base_plan', 'application.tasks.auto_move_users_to_base_plan',
                   'nurturing_warm_leads', 'application.tasks.nurturing_warm_leads']
        for name in retired + ['renew_memberships_payments']:
            PeriodicTask.objects.create(name=name, task=name, interval=interval, enabled=True)
        migration = importlib.import_module('application.migrations.0430_retire_software_plan_expiry')
        migration.disable_expiry_schedule(apps, SimpleNamespace(connection=connection))
        self.assertFalse(PeriodicTask.objects.filter(task__in=retired, enabled=True).exists())
        self.assertTrue(PeriodicTask.objects.get(task='renew_memberships_payments').enabled)
        self.assertFalse(BillingSubscription.objects.exists())

    def test_anonymous_is_denied(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/collaborators/list').status_code, (401, 403))

    def test_obsolete_expiry_task_cannot_send_renewal_email(self):
        from application.tasks import auto_move_users_to_base_plan
        plan = BillingPlan.objects.create(name='Expired Pro', billing_type=2)
        BillingSubscription.objects.create(user=self.owner, billing_plan=plan,
            ends_on=timezone.now() - timedelta(hours=1))
        with patch('application.tasks.send_email_template') as send:
            auto_move_users_to_base_plan()
        send.assert_not_called()

    @override_settings(FAMILY_REGISTRATION_ENABLED=True, FEDERATION_REGISTRATION_ENABLED=False,
                       MULTIPLE_REGISTRATION_ENABLED=False)
    def test_registration_features_come_from_instance_policy_not_customer_entitlements(self):
        from application.models import PreviewAndCustomFeatures
        feature = PreviewAndCustomFeatures.objects.create(name='Iscrizioni per Enti e Federazioni')
        self.owner.preview_and_custom_features.add(feature)
        names = [feature['name'] for feature in self.owner.get_preveiw_and_custom_features()]
        self.assertIn('Iscrizione per famiglia', names)
        self.assertNotIn('Iscrizioni per Enti e Federazioni', names)


@override_settings(INSTANCE_SETUP_TOKEN='contract-setup-token')
class SubscriptionIndependentSetupTests(TestCase):
    def test_fresh_setup_without_reference_plan(self):
        response = APIClient().post('/instance/configure', {
            'domain': 'fresh.example.test', 'oem': {'name': 'Fresh club'},
            'initialization': {'type': 'fresh', 'associationName': 'Fresh club',
                               'ownerEmail': 'owner@example.test', 'ownerPassword': 'ContractPass!123'},
        }, format='json', HTTP_X_SETUP_TOKEN='contract-setup-token')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(BillingPlan.objects.exists())
        self.assertFalse(BillingSubscription.objects.exists())
        client = APIClient()
        client.force_authenticate(InstanceConfiguration.objects.get().primary_association.user)
        self.assertEqual(client.get('/collaborators/list').status_code, 200)
