from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied
from auditlog.models import LogEntry

from application.impersonation import begin, end, resolve_target, eligible_users, session_key
from application.models import User, SportAssociation, Associate, Subscription, Course, Payment
from application.services.jwt_token_service import JWTTokenService


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class AssociationImpersonationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username='owner', role=User.ASSOCIATION)
        self.other_owner = User.objects.create_user(username='other-owner', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='Home')
        self.other = SportAssociation.objects.create(user=self.other_owner, denomination='Other')
        self.athlete = User.objects.create_user(username='athlete', role=User.ATHLETE)
        self.unrelated = User.objects.create_user(username='unrelated', role=User.ATHLETE)
        self.collaborator = User.objects.create_user(username='collaborator', role=User.COLLABORATOR,
            connected_user=self.owner, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE)
        self.admin = User.objects.create_superuser(username='admin', password='test')
        self.member = Associate.objects.create(user=self.athlete, sport_association=self.association,
            first_name='Shared', last_name='Athlete', tax_code='TEST')
        self.other_member = Associate.objects.create(user=self.athlete, sport_association=self.other,
            first_name='Shared', last_name='Athlete', tax_code='TEST')
        self.client = APIClient()
        self.authenticate(self.owner)

    def authenticate(self, user):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + JWTTokenService.generate_tokens_for_user(user)['access_token'])

    def start(self, user=None):
        user = user or self.athlete
        response = self.client.post('/association/impersonation', {'target_user_id': str(user.pk)}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.session = response.data['session_id']
        self.headers = {'HTTP_X_IMPERSONATION_ID': self.session, 'HTTP_USER_ID': str(user.pk)}
        return response

    def subscriptions(self):
        # Avoid enrollment callbacks; the test exercises the existing subscription records.
        values = []
        for association, member in ((self.association, self.member), (self.other, self.other_member)):
            subscription = Subscription(user=self.athlete, sport_association=association, associate=member,
                start_date=timezone.now(), end_date=timezone.now() + timezone.timedelta(days=365))
            subscription.save()
            values.append(subscription)
        return values

    def test_picker_and_session_share_eligibility(self):
        deleted = User.objects.create_user(username='deleted', role=User.ATHLETE, deleted=True)
        inactive = User.objects.create_user(username='inactive', role=User.ATHLETE, is_active=False)
        removed = User.objects.create_user(username='removed', role=User.ATHLETE)
        for user in (deleted, inactive, removed):
            Associate.objects.create(user=user, sport_association=self.association, deleted=user == removed)
        Associate.objects.create(sport_association=self.association)
        self.assertSetEqual(set(eligible_users(self.owner).values_list('pk', flat=True)), {self.athlete.pk, self.collaborator.pk})
        response = self.client.get('/association/impersonation/users', {'q': 'athlete'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([u['user_id'] for u in response.data['users']], [str(self.athlete.pk)])
        for target in (deleted, inactive, removed, self.unrelated, self.other_owner, self.owner, self.admin):
            with self.subTest(target=target.username):
                response = self.client.post('/association/impersonation', {'target_user_id': str(target.pk)}, format='json')
                self.assertEqual(response.status_code, 403, response.data)

    def test_profile_restoration_audit_and_session_context(self):
        response = self.start()
        self.assertEqual(response.data['actor_role'], 'association')
        self.assertEqual(response.data['association']['id'], str(self.association.pk))
        profile = self.client.get('/profile/info', **self.headers)
        self.assertEqual(profile.status_code, 200, profile.data)
        self.assertEqual(str(profile.data['user_data']['user_id']), str(self.athlete.pk))
        self.assertFalse(profile.data['user_data']['can_impersonate'])
        self.assertEqual(self.client.delete('/association/impersonation', **self.headers).status_code, 204)
        self.assertEqual(self.client.get('/profile/info', **self.headers).status_code, 403)
        profile = self.client.get('/profile/info')
        self.assertEqual(str(profile.data['user_data']['user_id']), str(self.owner.pk))
        self.assertTrue(profile.data['user_data']['can_impersonate'])
        events = LogEntry.objects.filter(action=LogEntry.Action.ACCESS, actor=self.owner)
        self.assertEqual(events.count(), 2)
        for event in events:
            self.assertEqual(event.additional_data['association_id'], str(self.association.pk))
            self.assertEqual(event.additional_data['target_user_id'], str(self.athlete.pk))

    def test_collaborators_cannot_initiate_and_permissions_are_preserved(self):
        self.authenticate(self.collaborator)
        self.assertEqual(self.client.get('/association/impersonation/users').status_code, 403)
        self.assertEqual(self.client.post('/association/impersonation', {'target_user_id': str(self.athlete.pk)}, format='json').status_code, 403)
        self.authenticate(self.owner)
        self.start(self.collaborator)
        self.assertEqual(self.client.get('/profile/info', **self.headers).status_code, 200)
        self.assertEqual(self.client.get('/statistic/dashboard', **self.headers).status_code, 403)
        # Switching is bound to the original owner, never a session for the collaborator.
        switched = self.client.post('/association/impersonation', {'target_user_id': str(self.athlete.pk)}, format='json', **self.headers)
        self.assertEqual(switched.status_code, 201, switched.data)
        with self.assertRaises(PermissionDenied):
            resolve_target(self.owner, self.session)
        with self.assertRaises(PermissionDenied):
            begin(resolve_target(self.owner, switched.data['session_id']), self.unrelated.pk)

    def test_membership_account_and_association_changes_revoke_access(self):
        self.start()
        for instance, field in ((self.member, 'deleted'), (self.athlete, 'deleted'), (self.owner, 'deleted'), (self.association, 'deleted')):
            with self.subTest(model=type(instance).__name__):
                setattr(instance, field, True)
                instance.save(update_fields=[field])
                with self.assertRaises(PermissionDenied):
                    resolve_target(self.owner, self.session)
                setattr(instance, field, False)
                instance.save(update_fields=[field])
        for user in (self.owner, self.athlete):
            user.is_active = False
            user.save(update_fields=['is_active'])
            with self.assertRaises(PermissionDenied):
                resolve_target(self.owner, self.session)
            user.is_active = True
            user.save(update_fields=['is_active'])
        cache.delete(session_key(self.session))
        response = self.client.get('/profile/info', **self.headers)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.data['impersonation_invalid'])
        self.assertEqual(self.client.delete('/association/impersonation', **self.headers).status_code, 204)

    def test_forgery_and_administration_access_are_rejected(self):
        self.start()
        for headers in ({'HTTP_USER_ID': str(self.athlete.pk)},
                        {**self.headers, 'HTTP_USER_ID': str(self.unrelated.pk)},
                        {**self.headers, 'HTTP_X_IMPERSONATION_ID': 'forged'}):
            self.assertEqual(self.client.get('/profile/info', **headers).status_code, 403)
        self.authenticate(self.other_owner)
        self.assertEqual(self.client.get('/profile/info', **self.headers).status_code, 403)
        self.authenticate(self.owner)
        for path in ('/administration/impersonation/users', '/instance/access'):
            self.assertEqual(self.client.get(path, **self.headers).status_code, 403)

    def test_multi_association_reads_and_writes(self):
        home, away = self.subscriptions()
        self.start()
        response = self.client.get('/subscription/list', **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        text = str(response.data)
        self.assertIn(str(home.pk), text)
        self.assertNotIn(str(away.pk), text)
        for path in (f'/subscription/{away.pk}/info', f'/subscription/{away.pk}/attendance',
                     f'/subscription/{away.pk}/calendar'):
            self.assertEqual(self.client.get(path, **self.headers).status_code, 403)
        with patch('application.views.subscriptions_views.print_document_subscription.delay'):
            allowed = self.client.patch(f'/subscription/{home.pk}/update', {'notes': 'By owner'}, format='json', **self.headers)
            denied = self.client.patch(f'/subscription/{away.pk}/update', {'notes': 'Cross tenant'}, format='json', **self.headers)
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(denied.status_code, 403, denied.data)
        home.refresh_from_db(); away.refresh_from_db()
        self.assertEqual(home.notes, 'By owner')
        self.assertNotEqual(away.notes, 'Cross tenant')
        event = LogEntry.objects.filter(object_pk=str(home.pk), action=LogEntry.Action.UPDATE).latest('pk')
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.additional_data['target_user_id'], str(self.athlete.pk))

    def test_payment_and_course_queries_cannot_select_another_association(self):
        home_payment = Payment.objects.create(user=self.athlete, associate=self.member,
            sport_association=self.association, amount=10)
        away_payment = Payment.objects.create(user=self.athlete, associate=self.other_member,
            sport_association=self.other, amount=20)
        self.start()
        response = self.client.get('/payment/list', **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(str(home_payment.pk), str(response.data))
        self.assertNotIn(str(away_payment.pk), str(response.data))
        denied = self.client.get('/course/list', {'sport_association_id': str(self.other.pk)}, **self.headers)
        self.assertEqual(denied.status_code, 403)
        denied = self.client.post('/stripe/multiple-pay', {'payments': [str(home_payment.pk), str(away_payment.pk)]}, format='json', **self.headers)
        self.assertEqual(denied.status_code, 403)

    def test_document_tokens_do_not_bypass_the_association_boundary(self):
        from docmanager.models import Document
        home, away = self.subscriptions()
        home.document_pdf = Document.objects.create(filename='home.pdf')
        away.document_pdf = Document.objects.create(filename='away.pdf')
        home.save(); away.save()
        self.start()
        with patch('docmanager.views.document_view.PrintingService') as printing:
            from rest_framework.response import Response
            printing.return_value.download_file.return_value = Response({'file': 'home'})
            allowed = self.client.get(f'/document/retrieve/{home.document_pdf_id}', **self.headers)
            self.assertEqual(allowed.status_code, 200, allowed.data)
            denied = self.client.get(f'/document/retrieve/{away.document_pdf_id}', {'token': str(away.document_pdf.token)}, **self.headers)
            self.assertEqual(denied.status_code, 403, denied.data)
            self.assertEqual(printing.return_value.download_file.call_count, 1)

    def test_editing_shared_medical_certificate_does_not_mutate_other_association(self):
        from application.models import MedicalCertificate
        home, away = self.subscriptions()
        certificate = MedicalCertificate.objects.create(user=self.athlete, notes='Shared original')
        home.medical = away.medical = certificate
        home.save(); away.save()
        self.start()
        response = self.client.post(f'/subscription/{home.pk}/medical-certificate/edit', {'notes': 'Home only'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 200, getattr(response, 'data', response.content))
        home.refresh_from_db(); away.refresh_from_db(); certificate.refresh_from_db()
        self.assertNotEqual(home.medical_id, away.medical_id)
        self.assertEqual(home.medical.notes, 'Home only')
        self.assertEqual(certificate.notes, 'Shared original')

    def test_expired_cache_entry_and_changed_collaborator_owner(self):
        self.start()
        cache.touch(session_key(self.session), timeout=-1)
        with self.assertRaises(PermissionDenied):
            resolve_target(self.owner, self.session)
        self.start(self.collaborator)
        self.collaborator.connected_user = self.other_owner
        self.collaborator.save(update_fields=['connected_user'])
        with self.assertRaises(PermissionDenied):
            resolve_target(self.owner, self.session)

    def test_scope_cannot_be_changed_inside_a_cached_session(self):
        self.start()
        record = cache.get(session_key(self.session))
        record['association_id'] = str(self.other.pk)
        cache.set(session_key(self.session), record)
        with self.assertRaises(PermissionDenied):
            resolve_target(self.owner, self.session)

    def test_dashboard_uses_initiator_instead_of_instance_primary_association(self):
        from instance.models import InstanceConfiguration
        InstanceConfiguration.objects.create(domain='test.local', name='Test', primary_association=self.other)
        self.start()
        response = self.client.get('/statistic/athlete-dashboard', **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(str(response.data['data'][0]['sport_association']['sport_association_id']), str(self.association.pk))
        self.assertNotIn(str(self.other.pk), str(response.data))

    def test_carnet_topups_and_absences_keep_target_permissions_and_tenant(self):
        from application.models import CourseSubscription, AttendanceRegistry, AttendanceDay
        from application.models.carnet_models import Carnet, CarnetSubscription
        home, away = self.subscriptions()
        carnets, days, registrations = [], [], []
        for subscription in (home, away):
            course = Course.objects.create(sport_association=subscription.sport_association, title='Course')
            registrations.append(CourseSubscription.objects.create(course=course, subscription=subscription))
            registry = AttendanceRegistry.objects.create(course=course)
            days.append(AttendanceDay.objects.create(attendance_registry=registry, date=timezone.now()))
            carnet = Carnet.objects.create(sport_association=subscription.sport_association, title='Lessons', fee=10, lessons_number=5)
            carnets.append(CarnetSubscription.objects.create(carnet_id=carnet, subscription=subscription, user_id=self.athlete))
        self.start()
        response = self.client.post(f'/carnet-subscription/{carnets[0].pk}/topup', {}, format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post(f'/carnet-subscription/{carnets[1].pk}/topup', {}, format='json', **self.headers)
        self.assertEqual(response.status_code, 403, response.data)
        response = self.client.post(f'/attendance-day/{days[0].pk}/mark-absent',
            {'course_subscription_id': str(registrations[0].pk)}, format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post(f'/attendance-day/{days[1].pk}/mark-absent',
            {'course_subscription_id': str(registrations[1].pk)}, format='json', **self.headers)
        self.assertEqual(response.status_code, 403, response.data)
        days[1].refresh_from_db()
        self.assertIsNone(days[1].expected_absences)

    def test_another_members_subscription_cannot_be_used_even_inside_own_association(self):
        other_member = Associate.objects.create(user=self.unrelated, sport_association=self.association, tax_code='UNRELATED')
        subscription = Subscription(user=self.unrelated, associate=other_member, sport_association=self.association)
        subscription.save()
        self.start()
        response = self.client.patch(f'/subscription/{subscription.pk}/update', {'notes': 'forged'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 403, response.data)
        self.member.deleted = True
        self.member.save(update_fields=['deleted'])
        response = self.client.get('/profile/info', **self.headers)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.data['impersonation_invalid'])


    def test_owner_can_end_own_session_after_association_revocation(self):
        self.start()
        self.association.deleted = True
        self.association.save(update_fields=['deleted'])
        response = self.client.delete('/association/impersonation', **self.headers)
        self.assertEqual(response.status_code, 204, getattr(response, 'data', None))
        self.assertIsNone(cache.get(session_key(self.session)))
        event = LogEntry.objects.filter(actor=self.owner, action=LogEntry.Action.ACCESS).latest('pk')
        self.assertEqual(event.additional_data['association_id'], str(self.association.pk))
