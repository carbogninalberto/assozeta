import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import assert_disposable, fixture_id, FIXTURE_VERSION
from application.manuale.tools import _context
from application.models import Course, Invoice, Payment, Subscription, Tags, User
from application.models.user_models import Associate, SportAssociation, Instructor, InstructorHours
from application.models.subscriptions_models import Signature, SubscriptionToken
from application.services.jwt_token_service import JWTTokenService
from docmanager.models import Document
from application.tests.base import BaseTestCase
from instance.models import InstanceConfiguration


class ManualSeedGuardTests(SimpleTestCase):
    def test_missing_run_and_unowned_database_are_rejected_before_mutation(self):
        with patch.dict(os.environ, {'ASSOZETA_MANUAL_RUN_ID': ''}):
            with self.assertRaises(CommandError):
                assert_disposable()
        with patch.dict(os.environ, {'ASSOZETA_MANUAL_RUN_ID': 'fixture0001'}):
            with self.assertRaises(CommandError):
                assert_disposable()


class ManualSeedTests(BaseTestCase):
    def test_actual_transfer_recipient_reads_uuid_owner_contract_and_foreign_subscription_is_denied(self):
        from freezegun import freeze_time
        clock = freeze_time('2026-09-30T12:00:00Z')
        clock.start()
        self.addCleanup(clock.stop)
        from application.tests.fixtures.factories import (
            create_test_user, create_test_sport_association,
            create_test_associate, create_test_subscription,
        )
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', scenario='member-transfer', output=str(output),
                origin='http://127.0.0.1:5010', verbosity=0)
            fixture = json.loads(output.read_text())
        owner = User.objects.get(pk=fixture_id('owner'))
        recipient = User.objects.get(pk=fixture_id('transfer-recipient'))
        member = Subscription.objects.get(pk=fixture_id('subscription-1'))
        foreign_owner = create_test_user(role=User.ASSOCIATION)
        foreign_association = create_test_sport_association(user=foreign_owner)
        foreign_person = create_test_associate(user=foreign_owner, tax_code='FRGNOWN80A01H501Z')
        foreign = create_test_subscription(sport_association=foreign_association,
            associate=foreign_person, user=foreign_owner)
        foreign_before = Subscription._base_manager.filter(pk=foreign.pk).values().get()
        other_before = list(Subscription._base_manager.exclude(pk=member.pk).order_by('pk').values())
        payments_before = list(Payment._base_manager.order_by('pk').values())
        association = APIClient()
        association.force_authenticate(owner)
        reader = APIClient()
        reader.credentials(HTTP_AUTHORIZATION='Bearer ' + fixture['identities']['reader']['token'])
        denied = reader.post(f'/subscription/{member.pk}/transfer', {'recipient': str(recipient.pk)})
        self.assertEqual(denied.status_code, 403, denied.content)
        with patch('notifications.services.NotificationService.send_notification'):
            transferred = association.post(f'/subscription/{member.pk}/transfer', {'recipient': str(recipient.pk)})
        self.assertEqual(transferred.status_code, 201, transferred.content)
        self.assertEqual(str(transferred.data['recipient']), str(recipient.pk))
        member.refresh_from_db()
        member.associate.refresh_from_db()
        self.assertEqual(member.user_id, recipient.pk)
        self.assertEqual(member.associate.user_id, recipient.pk)
        athlete = APIClient()
        athlete.force_authenticate(recipient)
        with patch('application.views.subscriptions_views.print_document_subscription.delay'):
            info = athlete.get(f'/subscription/{member.pk}/info')
        self.assertEqual(info.status_code, 200, info.content)
        self.assertEqual(info.json()['data']['info']['subscription_id'], str(member.pk))
        self.assertEqual(info.json()['data']['info']['user']['email'], recipient.email)
        rows = athlete.get('/subscription/list', {'pagination[perpage]': 100})
        self.assertEqual(rows.status_code, 200, rows.content)
        row = next(value for value in rows.json()['data'].values() if value['subscription_id'] == str(member.pk))
        self.assertIsInstance(row['user'], str)
        self.assertEqual(row['user'], str(recipient.pk))
        self.assertEqual(athlete.get(f'/subscription/{foreign.pk}/info').status_code, 403)
        self.assertEqual(association.post(f'/subscription/{member.pk}/transfer', {'recipient': str(owner.pk)}).status_code, 403)
        self.assertEqual(Subscription._base_manager.filter(pk=foreign.pk).values().get(), foreign_before)
        self.assertEqual(list(Subscription._base_manager.exclude(pk=member.pk).order_by('pk').values()), other_before)
        self.assertEqual(list(Payment._base_manager.order_by('pk').values()), payments_before)

    def test_existing_transfer_recipient_lookup_returns_expected_conflict_without_business_writes(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            call_command('seed_manuale', scenario='member-transfer',
                output=str(Path(temporary)/'browser.json'), origin='http://127.0.0.1:5010', verbosity=0)
        recipient = User.objects.get(pk=fixture_id('transfer-recipient'))
        models = (User, Associate, Subscription, Payment)
        before = {model: list(model._base_manager.order_by('pk').values()) for model in models}
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=fixture_id('owner')))
        response = client.get('/oauth2/check/email', {'email': recipient.email, 'get_user': '1'})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(response.data['valid'])
        self.assertEqual(response.data['exception'], 'email already taken.')
        self.assertEqual(str(response.data['user']['user_id']), str(recipient.pk))
        self.assertEqual(response.data['user']['email'], recipient.email)
        for model in models:
            self.assertEqual(list(model._base_manager.order_by('pk').values()), before[model], model.__name__)

    def test_transfer_profile_resets_its_recipient_and_links_without_touching_other_users(self):
        from application.models.subscriptions_models import SubscriptionTransfer
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'), \
                patch('redis.Redis') as redis_client, patch('notifications.services.NotificationService.invalidate_user_broadcasts_cache'):
            output = Path(temporary) / 'browser.json'
            kwargs = {'output': str(output), 'origin': 'http://127.0.0.1:5010', 'verbosity': 0}
            call_command('seed_manuale', scenario='member-transfer', **kwargs)
            data = json.loads(output.read_text())
            owner = User.objects.get(pk=fixture_id('owner'))
            recipient = User.objects.get(pk=fixture_id('transfer-recipient'))
            self.assertEqual(data['fixture_profile'], 'member-transfer')
            self.assertEqual(data['identities']['recipient']['user_id'], str(recipient.pk))
            self.assertEqual(data['identities']['recipient']['email'], recipient.email)
            self.assertEqual(recipient.role, User.ATHLETE)
            self.assertIsNone(recipient.connected_user_id)
            foreign = User.objects.create_user(username='transfer-preserved', email='transfer.preserved@example.test')
            member = Subscription.objects.get(pk=fixture_id('subscription-1'))
            member.user = recipient
            member.save(update_fields=['user'])
            member.associate.user = recipient
            member.associate.save(update_fields=['user'])
            SubscriptionTransfer.objects.create(subscription=member, requester=owner, recipient=recipient,
                status=SubscriptionTransfer.ACCEPTED)
            call_command('seed_manuale', scenario='member-transfer', **kwargs)
            member.refresh_from_db()
            member.associate.refresh_from_db()
            self.assertEqual(member.user_id, owner.pk)
            self.assertIsNone(member.associate.user_id)
            self.assertFalse(SubscriptionTransfer.objects.filter(subscription=member).exists())
            self.assertEqual(User.objects.filter(pk=fixture_id('transfer-recipient')).count(), 1)
            self.assertTrue(User.objects.filter(pk=foreign.pk).exists())
            redis_client.return_value.delete.assert_called_once_with('broadcast_' + str(recipient.pk))
            call_command('seed_manuale', **kwargs)
            self.assertFalse(User._base_manager.filter(pk=recipient.pk).exists())
            self.assertNotIn('recipient', json.loads(output.read_text())['identities'])
            self.assertTrue(User.objects.filter(pk=foreign.pk).exists())

    def test_transfer_profile_refuses_a_foreign_identity_and_foreign_membership(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'), \
                patch('redis.Redis') as redis_client, patch('notifications.services.NotificationService.invalidate_user_broadcasts_cache'):
            output = Path(temporary) / 'browser.json'
            kwargs = {'output': str(output), 'origin': 'http://127.0.0.1:5010', 'verbosity': 0}
            call_command('seed_manuale', **kwargs)
            collision = User.objects.create_user(pk=fixture_id('transfer-recipient'),
                username='foreign-recipient', email='foreign.recipient@example.test', role=User.ATHLETE)
            with self.assertRaisesRegex(CommandError, 'another user'):
                call_command('seed_manuale', scenario='member-transfer', **kwargs)
            collision.refresh_from_db()
            self.assertEqual(collision.username, 'foreign-recipient')
            redis_client.assert_not_called()
            collision.delete()
            call_command('seed_manuale', scenario='member-transfer', **kwargs)
            recipient = User.objects.get(pk=fixture_id('transfer-recipient'))
            foreign_owner = User.objects.create_user(username='transfer-foreign-owner', email='foreign.owner@example.test')
            foreign_association = SportAssociation.objects.create(user=foreign_owner, denomination='Preserved')
            foreign_person = Associate.objects.create(sport_association=foreign_association,
                first_name='Foreign', last_name='Recipient')
            foreign_member = Subscription.objects.create(sport_association=foreign_association,
                associate=foreign_person, user=recipient)
            with self.assertRaisesRegex(CommandError, 'unrelated ownership'):
                call_command('seed_manuale', **kwargs)
            foreign_member.refresh_from_db()
            self.assertEqual(foreign_member.user_id, recipient.pk)
            self.assertTrue(User.objects.filter(pk=recipient.pk).exists())
            redis_client.assert_not_called()

    def test_removal_profile_is_opt_in_repeatable_and_baseline_removes_only_its_records(self):
        from application.models.user_models import CollaborationInvites
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            kwargs = {'output': str(output), 'origin': 'http://127.0.0.1:5010', 'verbosity': 0}
            call_command('seed_manuale', **kwargs)
            owner = User.objects.get(pk=fixture_id('owner'))
            reader_fields = ('username', 'first_name', 'last_name', 'connected_user_id', 'collaborator_role', 'collaborator_permissions')
            baseline_reader = User.objects.values(*reader_fields).get(pk=fixture_id('reader'))
            self.assertNotIn('removable', json.loads(output.read_text())['identities'])
            foreign_owner = User.objects.create_user(username='removal-foreign', email='removal.foreign@example.test')
            foreign_user = User.objects.create_user(username='foreign-collaborator', email='foreign.collaborator@example.test',
                role=User.COLLABORATOR, connected_user=foreign_owner)
            foreign_invite = CollaborationInvites.objects.create(user=foreign_owner, email='foreign.invite@example.test')
            for _ in range(2):
                call_command('seed_manuale', scenario='collaborator-removal', **kwargs)
                data = json.loads(output.read_text())
                self.assertEqual(data['fixture_profile'], 'collaborator-removal')
                self.assertEqual(data['identities']['removable']['user_id'], str(fixture_id('removable-collaborator')))
                self.assertEqual(data['removable_invite_id'], str(fixture_id('removable-invite')))
                self.assertEqual(User.objects.filter(connected_user=owner).count(), 2)
                self.assertEqual(CollaborationInvites.objects.filter(user=owner).count(), 1)
                self.assertEqual(User.objects.values(*reader_fields).get(pk=fixture_id('reader')), baseline_reader)
            call_command('seed_manuale', **kwargs)
            self.assertFalse(User._base_manager.filter(pk=fixture_id('removable-collaborator')).exists())
            self.assertFalse(CollaborationInvites.objects.filter(pk=fixture_id('removable-invite')).exists())
            self.assertEqual(User.objects.filter(connected_user=owner).count(), 1)
            self.assertTrue(User.objects.filter(pk=foreign_user.pk).exists())
            self.assertTrue(CollaborationInvites.objects.filter(pk=foreign_invite.pk).exists())
            self.assertNotIn('removable_invite_id', json.loads(output.read_text()))

    def test_removal_fixture_does_not_overwrite_foreign_owner_identity_or_invitation(self):
        from application.models.user_models import CollaborationInvites
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            kwargs = {'output': str(output), 'origin': 'http://127.0.0.1:5010', 'verbosity': 0}
            call_command('seed_manuale', **kwargs)
            foreign_owner = User.objects.create_user(username='collision-foreign', email='collision.foreign@example.test')
            foreign_user = User.objects.create_user(pk=fixture_id('removable-collaborator'),
                username='collision-collaborator', email='collision.collaborator@example.test',
                role=User.COLLABORATOR, connected_user=foreign_owner)
            with self.assertRaisesRegex(CommandError, 'another owner'):
                call_command('seed_manuale', scenario='collaborator-removal', **kwargs)
            foreign_user.refresh_from_db()
            self.assertEqual(foreign_user.connected_user_id, foreign_owner.pk)
            self.assertEqual(foreign_user.username, 'collision-collaborator')
            foreign_user.delete()
            foreign_invite = CollaborationInvites.objects.create(pk=fixture_id('removable-invite'),
                user=foreign_owner, email='collision.invite@example.test')
            with self.assertRaisesRegex(CommandError, 'another owner'):
                call_command('seed_manuale', scenario='collaborator-removal', **kwargs)
            foreign_invite.refresh_from_db()
            self.assertEqual(foreign_invite.user_id, foreign_owner.pk)
            self.assertFalse(User._base_manager.filter(pk=fixture_id('removable-collaborator')).exists())

    def test_reset_clears_owned_calendar_carnets_and_hidden_enrollment_preserving_foreign_state(self):
        from django.utils import timezone
        from application.models.attendee_models import AttendanceRegistry, AttendanceDay, Reminders
        from application.models.carnet_models import Carnet, CarnetSubscription
        from application.models.courses_models import CourseSubscription
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            owner = User.objects.get(pk=fixture_id('owner'))
            reader = User.objects.get(pk=fixture_id('reader'))
            association = SportAssociation.objects.get(pk=fixture_id('association'))
            course = Course.objects.get(pk=fixture_id('course'))
            member = Subscription.objects.get(pk=fixture_id('subscription-1'))
            other_owner = User.objects.create_user(username='calendar-preserved-owner', email='calendar@example.test',
                dashboard_layout=[{'id': 'foreign', 'width': 4}])
            other_association = SportAssociation.objects.create(user=other_owner, denomination='Preserved calendar')
            other_person = Associate.objects.create(sport_association=other_association,
                first_name='Preserved', last_name='Member')
            other_member = Subscription.objects.create(sport_association=other_association,
                associate=other_person, user=other_owner)
            other_course = Course.objects.create(sport_association=other_association, title='Preserved')

            def calendar_state(actor, tenant, activity, registration):
                enrollment = CourseSubscription.objects.create(course=activity, subscription=registration, deleted=True)
                registry = AttendanceRegistry.objects.create(course=activity, events=[], status=AttendanceRegistry.PUBLISHED)
                day = AttendanceDay.objects.create(attendance_registry=registry, title='Demo', date=timezone.now(), attendees=[])
                reminder = Reminders.objects.create(user=actor, sport_association=tenant, event_title='Demo', send_at=timezone.now())
                carnet = Carnet.objects.create(user_id=actor, sport_association=tenant, title='Demo', lessons_number=5, fee=50)
                assigned = CarnetSubscription.objects.create(user_id=actor, subscription=registration, carnet_id=carnet,
                    meta={'lessons_left': 4})
                assigned.course_subscription.add(enrollment)
                return [enrollment, registry, day, reminder, carnet, assigned]

            owned = calendar_state(owner, association, course, member)
            foreign = calendar_state(other_owner, other_association, other_course, other_member)
            from application.models.subscriptions_models import AssociateImportDraft, AssociateImportDraftStatus
            for model in (AssociateImportDraft, AssociateImportDraftStatus):
                owned.append(model.objects.create(sport_association=association))
                foreign.insert(-1, model.objects.create(sport_association=other_association))
            for actor in (owner, reader):
                actor.dashboard_layout = [{'id': 'changed', 'width': 4}]
                actor.save(update_fields=['dashboard_layout'])
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            for record in owned:
                self.assertFalse(type(record)._base_manager.filter(pk=record.pk).exists())
            for record in foreign:
                self.assertTrue(type(record)._base_manager.filter(pk=record.pk).exists())
            foreign[0].refresh_from_db()
            foreign[-1].refresh_from_db()
            self.assertTrue(foreign[0].deleted)
            self.assertEqual(foreign[-1].meta, {'lessons_left': 4})
            self.assertTrue(foreign[-1].course_subscription.through.objects.filter(
                carnetsubscription=foreign[-1], coursesubscription=foreign[0]).exists())
            for actor in (owner, reader):
                actor.refresh_from_db()
                self.assertIsNone(actor.dashboard_layout)
            other_owner.refresh_from_db()
            self.assertEqual(other_owner.dashboard_layout, [{'id': 'foreign', 'width': 4}])
            self.assertEqual(json.loads(output.read_text())['fixture_version'], FIXTURE_VERSION)

    def test_reset_removes_owned_instructors_and_hours_preserving_foreign_instructor(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            owner = User.objects.get(pk=fixture_id('owner'))
            instructor = Instructor.objects.create(user=owner, first_name='Paola', last_name='Neri')
            hours = InstructorHours.objects.create(instructor=instructor, hours='2.50', hourly_billing='20.00')
            foreign_owner = User.objects.create_user(username='instructor-preserved-owner', email='instructor@example.test')
            foreign_instructor = Instructor.objects.create(user=foreign_owner, first_name='Preserved', last_name='Instructor')
            foreign_hours = InstructorHours.objects.create(instructor=foreign_instructor, hours='3.00', hourly_billing='25.00')
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            self.assertFalse(Instructor.objects.filter(pk=instructor.pk).exists())
            self.assertFalse(InstructorHours.objects.filter(pk=hours.pk).exists())
            self.assertTrue(Instructor.objects.filter(pk=foreign_instructor.pk).exists())
            foreign_hours.refresh_from_db()
            self.assertEqual(str(foreign_hours.hours), '3.00')
            self.assertEqual(str(foreign_hours.hourly_billing), '25.00')
            self.assertEqual(foreign_hours.instructor_id, foreign_instructor.pk)

    def test_reset_removes_created_people_signatures_and_hidden_registrations_only_in_owned_association(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            owner = User.objects.get(pk=fixture_id('owner'))
            association = SportAssociation.objects.get(pk=fixture_id('association'))
            created_person = Associate.objects.create(sport_association=association, first_name='Marta', last_name='Neri')
            created_registration = Subscription.objects.create(sport_association=association, associate=created_person,
                user=owner, type=Subscription.ASSOCIATE_AND_MEMBER, status_flag=Subscription.PENDING, deleted=True)
            created_signature = Signature.objects.create(user=owner, signature='fictional signature reset fixture')
            stable = Subscription.objects.get(pk=fixture_id('subscription-1'))
            stable.deleted = True
            stable.archived = True
            stable.save(update_fields=['deleted', 'archived'])
            other_owner = User.objects.create_user(username='preserved-owner', email='preserved@example.test')
            other_association = SportAssociation.objects.create(user=other_owner, denomination='Preserved association')
            other_person = Associate.objects.create(sport_association=other_association, first_name='Preserved')
            other_registration = Subscription.objects.create(sport_association=other_association, associate=other_person,
                user=other_owner, status_flag=Subscription.PENDING)
            other_signature = Signature.objects.create(user=other_owner, signature='preserved fixture signature')
            owned_token = SubscriptionToken.objects.create(subscription=stable)
            foreign_token = SubscriptionToken.objects.create(subscription=other_registration)
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            self.assertFalse(Subscription._base_manager.filter(pk=created_registration.pk).exists())
            self.assertFalse(Associate._base_manager.filter(pk=created_person.pk).exists())
            self.assertFalse(Signature.objects.filter(pk=created_signature.pk).exists())
            self.assertFalse(SubscriptionToken.objects.filter(pk=owned_token.pk).exists())
            self.assertTrue(SubscriptionToken.objects.filter(pk=foreign_token.pk).exists())
            self.assertTrue(Subscription.objects.filter(pk=other_registration.pk).exists())
            self.assertTrue(Associate.objects.filter(pk=other_person.pk).exists())
            self.assertTrue(Signature.objects.filter(pk=other_signature.pk).exists())
            stable.refresh_from_db()
            self.assertFalse(stable.deleted)
            self.assertFalse(stable.archived)
            self.assertEqual(Subscription.objects.filter(sport_association=association).count(), 3)
            self.assertEqual(Associate.objects.filter(sport_association=association).count(), 3)

    def test_actual_creation_handler_keeps_existing_account_and_unsigned_request_unapproved(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            call_command('seed_manuale', output=str(Path(temporary) / 'browser.json'),
                origin='http://127.0.0.1:5010', verbosity=0)
        owner = User.objects.get(pk=fixture_id('owner'))
        client = APIClient()
        client.force_authenticate(user=owner)
        calculation = client.post('/subscription/calculate-tax-code', {'first_name': 'Marta', 'last_name': 'Neri',
            'sex': 'F', 'born_date': '2000-01-01', 'born_city': 'Roma'}, format='json')
        self.assertEqual(calculation.status_code, 200, calculation.content)
        response = client.post('/subscription/add', {
            'new_user_account': {'new_member': False},
            'associate_data': {'type': Subscription.ASSOCIATE_AND_MEMBER, 'role': Subscription.SOCIO_ORDINARIO,
                'first_name': 'Marta', 'last_name': 'Neri', 'sex': 'F', 'born_date': '01/01/2000',
                'born_city': 'Roma', 'tax_code': calculation.data['tax_code'], 'is_minor': False,
                'address': 'Via delle Attività 4', 'address_city': 'Roma', 'address_cap': '00100',
                'email': 'marta@example.test'},
            'associate_tutor_data': None, 'signature': {'there_is_signature': False, 'data': ''},
            'medical_certificate': {'medical_id': None},
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        subscription = Subscription.objects.get(associate__first_name='Marta', sport_association_id=fixture_id('association'))
        self.assertEqual(subscription.user_id, owner.pk)
        self.assertEqual(subscription.status_flag, Subscription.NOT_SIGNED)
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(subscription.associate.email, 'marta@example.test')
        self.assertEqual(subscription.payment_id, response.data['payment_id'])
        self.assertEqual(str(subscription.payment.amount), '25.00')
        self.assertFalse(subscription.payment.paid)
        reader = User.objects.get(pk=fixture_id('reader'))
        # This public signup endpoint scopes signed-in callers through JWT
        # authentication, which force_authenticate deliberately bypasses.
        reader_client = APIClient()
        access_token = JWTTokenService.generate_tokens_for_user(reader)['access_token']
        reader_client.credentials(HTTP_AUTHORIZATION='Bearer ' + access_token)
        denied = reader_client.post('/subscription/add', {}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(Subscription.objects.count(), 4)

    @patch('application.signals.check_workflows_trigger.delay')
    def test_actual_approval_handler_saves_acceptance_without_collecting_fee_and_rejects_second_approval(self, enqueue):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            call_command('seed_manuale', output=str(Path(temporary) / 'browser.json'),
                origin='http://127.0.0.1:5010', verbosity=0)
        subscription = Subscription.objects.get(pk=fixture_id('subscription-3'))
        subscription.status_flag = Subscription.PENDING
        subscription.acceptance_date = None
        subscription.save(update_fields=['status_flag', 'acceptance_date'])
        owner = User.objects.get(pk=fixture_id('owner'))
        client = APIClient()
        client.force_authenticate(user=owner)
        response = client.post(f'/subscription/{subscription.pk}/approve', {}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status_flag, Subscription.ACCEPTED)
        accepted_at = subscription.acceptance_date
        self.assertIsNotNone(accepted_at)
        self.assertFalse(subscription.payment.paid)
        self.assertEqual(str(subscription.payment.amount), '25.00')
        repeated = client.post(f'/subscription/{subscription.pk}/approve', {}, format='json')
        self.assertEqual(repeated.status_code, 403)
        client.force_authenticate(user=User.objects.get(pk=fixture_id('reader')))
        denied = client.post(f'/subscription/{subscription.pk}/approve', {}, format='json')
        self.assertEqual(denied.status_code, 403)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status_flag, Subscription.ACCEPTED)
        self.assertEqual(subscription.acceptance_date, accepted_at)
        enqueue.assert_called_once_with(trigger_type='subscription_approved',
            sport_association_id=fixture_id('association'), subscription_id=subscription.pk)

    def test_seed_is_repeatable_resets_scenario_tags_and_writes_only_private_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'browser.json'
            # Writes are within this test's transaction-owned database. The
            # production guard is exercised independently above.
            with patch('application.management.commands.seed_manuale.assert_disposable'):
                call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
                first = Subscription.objects.get(pk=fixture_id('subscription-1'))
                tag = Tags.objects.create(tag_name='Scenario-created', sport_association=first.sport_association)
                first.tags.add(tag)
                original_credentials = json.loads(output.read_text())
                owner = User.objects.get(pk=fixture_id('owner'))
                self.assertTrue(owner.check_password(original_credentials['login_password']))
                owner.two_fa = True
                owner.two_fa_secret = 'DEMONSTRATIONSECRET'
                owner.save(update_fields=['two_fa', 'two_fa_secret'])
                call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            credentials = json.loads(output.read_text())
            owner.refresh_from_db()
            self.assertEqual(credentials['login_username'], owner.username)
            self.assertTrue(owner.check_password(credentials['login_password']))
            self.assertNotEqual(credentials['login_password'], original_credentials['login_password'])
            self.assertFalse(owner.check_password(original_credentials['login_password']))
            self.assertFalse(owner.two_fa)
            self.assertEqual(owner.two_fa_secret, '')
            self.assertEqual(User.objects.count(), 2)
            self.assertEqual(Subscription.objects.count(), 3)
            self.assertEqual(Payment.objects.count(), 3)
            self.assertEqual(Course.objects.count(), 1)
            self.assertEqual(Tags.objects.count(), 0)
            self.assertEqual(InstanceConfiguration.get_config().primary_association_id, fixture_id('association'))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text())['identities']['reader']['user_id'], str(fixture_id('reader')))
            self.assertEqual(User.objects.get(pk=fixture_id('reader')).collaborator_permissions,
                ['association.dashboard.read', 'association.members.read', 'association.members.archive.read',
                 'association.courses.read', 'bookeeping.payments.read', 'bookeeping.documents.invoices.read',
                 'association.instructor.read', 'association.instructor.hours.read', 'association.carnet.read',
                 'association.courses.attendance.read', 'other.settings.read', 'other.users.collaborators.read',
                 'association.campsandretreats.read', 'association.calendar.read', 'association.events.read'])
            from freezegun import freeze_time
            from rest_framework_simplejwt.tokens import AccessToken
            with freeze_time('2026-09-30 12:00:00+00:00'):
                self.assertEqual(AccessToken(json.loads(output.read_text())['token'])['token_type'], 'access')
            self.assertEqual(Course.objects.get(pk=fixture_id('course')).creation_date.isoformat(), '2026-09-30T12:00:00+00:00')

    def test_reset_removes_created_and_soft_deleted_financial_state_and_restores_payment_settings(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            payment = Payment.objects.get(pk=fixture_id('payment-3'))
            association = payment.sport_association
            document = Document.objects.create(filename='ricevuta-demo.pdf')
            unrelated_document = Document.objects.create(filename='documento-da-conservare.pdf')
            invoice = Invoice.objects.create(sport_association=association, membership_fee=25, activity_fee=0,
                number=7, document_pdf=document, deleted=True)
            payment.invoice = invoice
            payment.paid = True
            payment.amount = 99
            payment.deleted = True
            payment.save()
            extra = Payment.objects.create(sport_association=association, amount=11, description='Scenario')
            extra.deleted = True
            extra.save()
            owner = association.user
            owner.auto_paid_payment = True
            owner.starting_number_invoices = 99
            owner.payment_date_equal_invoice_date = True
            owner.save()
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            self.assertEqual(Payment._base_manager.count(), 3)
            self.assertEqual(Invoice._base_manager.count(), 0)
            self.assertFalse(Document.objects.filter(pk=document.pk).exists())
            self.assertTrue(Document.objects.filter(pk=unrelated_document.pk).exists())
            payment.refresh_from_db()
            self.assertFalse(payment.paid)
            self.assertFalse(payment.deleted)
            self.assertIsNone(payment.invoice_id)
            self.assertIsNone(payment.payment_date)
            self.assertEqual(str(payment.amount), '25.00')
            self.assertEqual(payment.custom_accounts_id, fixture_id('cash-account'))
            self.assertEqual(payment.payment_category_id, fixture_id('payment-category'))
            owner.refresh_from_db()
            self.assertFalse(owner.auto_paid_payment)
            self.assertEqual(owner.starting_number_invoices, 0)
            self.assertFalse(owner.payment_date_equal_invoice_date)

    def test_maintainer_evidence_requires_active_actual_instance_owner(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            call_command('seed_manuale', output=str(Path(temporary) / 'browser.json'), origin='http://127.0.0.1:5010', verbosity=0)
        owner = User.objects.get(pk=fixture_id('owner'))
        with override_settings(MANUAL_APPLICATION_REVISION='fixture-revision', MANUAL_SOURCE_ROOT=''):
            self.assertTrue(_context(fixture_id('association'), owner.pk)['maintainer'])
            self.assertFalse(_context('another-association', owner.pk)['maintainer'])
            self.assertFalse(_context(fixture_id('association'))['maintainer'])
            other = User.objects.create_user(username='another-owner', email='another@example.test', is_superuser=True)
            self.assertFalse(_context(fixture_id('association'), other.pk)['maintainer'])
            owner.is_active = False
            owner.save(update_fields=['is_active'])
            self.assertFalse(_context(fixture_id('association'), owner.pk)['maintainer'])

    def test_older_receipt_profile_is_repeatable_and_baseline_removes_it(self):
        from datetime import datetime, timedelta, timezone
        reference = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            for _ in range(2):
                call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010',
                             scenario='receipts-edit-delete', verbosity=0)
                data = json.loads(output.read_text())
                self.assertEqual(data['fixture_version'], FIXTURE_VERSION)
                self.assertEqual(data['fixture_profile'], 'receipts-edit-delete')
                self.assertEqual(data['older_receipt_id'], str(fixture_id('older-receipt')))
                self.assertEqual(Invoice._base_manager.count(), 1)
                invoice = Invoice.objects.get(pk=fixture_id('older-receipt'))
                self.assertEqual(invoice.creation_date, reference - timedelta(days=29))
                self.assertEqual(invoice.number, 7)
                self.assertIsNone(invoice.document_pdf_id)
                payment = Payment.objects.get(pk=fixture_id('payment-1'))
                self.assertEqual(payment.invoice_id, invoice.pk)
                self.assertTrue(payment.paid)
                self.assertEqual(payment.payment_date, invoice.creation_date)
                self.assertFalse(User.objects.get(pk=fixture_id('owner')).temporary_invoice_deletion)
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010', verbosity=0)
            self.assertEqual(Invoice._base_manager.count(), 0)
            data = json.loads(output.read_text())
            self.assertEqual(data['fixture_profile'], 'baseline')
            self.assertIsNone(data['older_receipt_id'])
            payment.refresh_from_db()
            self.assertIsNone(payment.invoice_id)
            self.assertTrue(payment.paid)
            self.assertEqual(payment.creation_date, reference)
            self.assertTrue(User.objects.get(pk=fixture_id('owner')).temporary_invoice_deletion)

    def test_actual_delete_handler_accepts_older_receipt_with_flag_disabled_and_reopens_payment(self):
        # This checks the real handler under transaction-owned test data.
        # Browser evidence separately proves the JWT and UI workflow.
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            call_command('seed_manuale', output=str(Path(temporary) / 'browser.json'),
                         origin='http://127.0.0.1:5010', scenario='receipts-edit-delete', verbosity=0)
        payment = Payment.objects.get(pk=fixture_id('payment-1'))
        amount = payment.amount
        payment_date = payment.payment_date
        owner = User.objects.get(pk=fixture_id('owner'))
        self.assertFalse(owner.temporary_invoice_deletion)
        client = APIClient()
        client.force_authenticate(user=owner)
        response = client.post(f'/invoice/{fixture_id("older-receipt")}/delete', {}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Invoice.objects.filter(pk=fixture_id('older-receipt')).exists())
        payment.refresh_from_db()
        self.assertFalse(payment.paid)
        self.assertIsNone(payment.invoice_id)
        self.assertEqual(payment.amount, amount)
        self.assertEqual(payment.payment_date, payment_date)
