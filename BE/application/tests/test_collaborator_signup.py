"""Invitation redemption and account-email regression coverage; no external sends."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from application.models import User, SportAssociation
from application.models.user_models import CollaborationInvites
from application.views.auth_views import AuthUtils, _signup_collaborator
from application.services.jwt_token_service import JWTTokenService
from instance.models import InstanceConfiguration


class InvitationFixture:
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(username='OWNER', email='owner@example.test', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='Test ASD', tax_code='12345678901')
        self.invite = CollaborationInvites.objects.create(
            user=self.owner, email='invited@example.test', token='regression-invite',
            expiration_date=timezone.now() + timedelta(days=1),
            collaborator_role=User.CUSTOM_COLLABORATOR_ROLE, collaborator_permissions=['example.permission'],
        )
        self.payload = dict(first_name='Test', last_name='Invite', username='INVITED', email='INVITED@example.test',
                            password='StrongPass!123', sport_association=False, collaboratorToken=self.invite.token)
        self.client = APIClient()
        self.real_generate = JWTTokenService.generate_tokens_for_user
        self.real_response = JWTTokenService.build_login_response
        for target, kwargs in [
            ('application.views.auth_views.JWTTokenService.generate_tokens_for_user', {'return_value': {}}),
            ('application.views.auth_views.JWTTokenService.build_login_response', {'side_effect': lambda *args: {'user_data': {}}}),
            ('application.views.auth_views.AuthUtils.send_welcome_email', {}),
        ]:
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def submit(self, **changes):
        return self.client.post('/oauth2/signup', {**self.payload, **changes}, format='json')


@override_settings(DEBUG=True)
class CollaboratorSignupTests(InvitationFixture, TestCase):
    def test_success_assigns_invitation_permissions_and_consumes_once(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.data)
        user = User.objects.get(username='INVITED')
        self.assertEqual(user.role, User.COLLABORATOR)
        self.assertEqual(user.connected_user, self.owner)
        self.assertEqual(user.collaborator_role, self.invite.collaborator_role)
        self.assertEqual(user.collaborator_permissions, self.invite.collaborator_permissions)
        self.assertFalse(response.data['user_data']['requires_welcome'])
        self.assertFalse(CollaborationInvites.objects.filter(pk=self.invite.pk).exists())
        self.assertEqual(self.submit().data['code'], 'invite_not_found')

    def test_signup_returns_real_tokens_and_sends_welcome_after_commit(self):
        with patch.object(JWTTokenService, 'generate_tokens_for_user', side_effect=self.real_generate), \
                patch.object(JWTTokenService, 'build_login_response', side_effect=self.real_response):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.submit()
                AuthUtils.send_welcome_email.assert_not_called()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('access_token', response.data)
        self.assertIn('refresh_token', response.data)
        # Collaborators intentionally share the association UI; JWT retains the distinct role.
        self.assertEqual(response.data['role'], 'association')
        from rest_framework_simplejwt.tokens import AccessToken
        self.assertEqual(AccessToken(response.data['access_token'])['role'], User.COLLABORATOR)
        AuthUtils.send_welcome_email.assert_called_once()

    def test_unusable_invitations_do_not_create_users(self):
        for changes, code in [({'collaboratorToken': 'missing'}, 'invite_not_found'),
                              ({'collaboratorToken': ''}, 'invite_not_found'),
                              ({'email': 'other@example.test'}, 'invite_email_mismatch')]:
            with self.subTest(code=code):
                response = self.submit(**changes)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['code'], code)
        for fields, code in [({'accepted': True}, 'invite_used'),
                             ({'accepted': False, 'expiration_date': timezone.now() - timedelta(seconds=1)}, 'invite_expired'),
                             ({'expiration_date': None}, 'invite_expired')]:
            CollaborationInvites.objects.filter(pk=self.invite.pk).update(**fields)
            self.assertEqual(self.submit().data['code'], code)
        self.assertFalse(User.objects.filter(username='INVITED').exists())
        self.assertTrue(CollaborationInvites.objects.filter(pk=self.invite.pk).exists())

    def test_creation_failure_rolls_back_and_allows_retry(self):
        original = User.objects.create_user
        def fail_after_creation(**kwargs):
            original(**kwargs)
            raise ValueError('simulated failure')
        with patch.object(User.objects, 'create_user', side_effect=fail_after_creation):
            self.assertEqual(self.submit().data['code'], 'signup_failed')
        self.assertFalse(User.objects.filter(username='INVITED').exists())
        self.assertTrue(CollaborationInvites.objects.filter(pk=self.invite.pk).exists())
        self.assertEqual(self.submit().status_code, 200)

    def test_invalid_fields_preserve_invitation(self):
        self.assertEqual(self.submit(password='weak').status_code, 400)
        self.assertTrue(CollaborationInvites.objects.filter(pk=self.invite.pk).exists())

    def test_ordinary_signup_still_creates_an_athlete(self):
        payload = {key: value for key, value in self.payload.items() if key != 'collaboratorToken'}
        response = self.client.post('/oauth2/signup', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        user = User.objects.get(username='INVITED')
        self.assertEqual(user.role, User.ATHLETE)
        self.assertIsNone(user.connected_user)
        self.assertTrue(CollaborationInvites.objects.filter(pk=self.invite.pk).exists())

    def test_social_signup_uses_verified_identity_and_invitation(self):
        for backend, method in [('google', 'verify_google_token'), ('apple', 'verify_apple_token')]:
            with self.subTest(backend=backend):
                identity = {'email': 'someone@example.test', 'email_verified': True}
                with patch('application.views.auth_views.SocialAuthService.' + method, return_value=(True, identity)):
                    self.assertEqual(self.submit(backend=backend, token='provider-token').data['code'], 'invite_email_mismatch')
        identity = {'email': self.invite.email, 'email_verified': True}
        with patch('application.views.auth_views.SocialAuthService.verify_google_token', return_value=(True, identity)):
            self.assertEqual(self.submit(backend='google', token='provider-token').status_code, 200)
        user = User.objects.get(username='INVITED')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.connected_user, self.owner)
        self.assertEqual(user.collaborator_permissions, self.invite.collaborator_permissions)

    def test_social_identity_must_be_verified_and_existing_account_is_unchanged(self):
        existing = User.objects.create_user(username='EXISTING', email=self.invite.email, role=User.ATHLETE)
        identity = {'email': self.invite.email, 'email_verified': False}
        with patch('application.views.auth_views.SocialAuthService.verify_google_token', return_value=(True, identity)):
            self.assertEqual(self.submit(backend='google', token='provider-token').data['code'], 'invalid_social_identity')
            identity['email_verified'] = True
            self.assertEqual(self.submit(backend='google', token='provider-token').data['code'], 'account_exists')
        existing.refresh_from_db()
        self.assertEqual(existing.role, User.ATHLETE)
        self.assertIsNone(existing.connected_user)
        self.assertTrue(CollaborationInvites.objects.filter(pk=self.invite.pk).exists())


@override_settings(DEBUG=True)
class ConcurrentInvitationTests(InvitationFixture, TransactionTestCase):
    def test_two_redemptions_create_exactly_one_account(self):
        from django.db import connection
        if not connection.features.has_select_for_update:
            self.skipTest('Requires row-locking database')
        barrier = Barrier(2)
        def redeem(username):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return _signup_collaborator({**self.payload, 'username': username}).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(redeem, ['FIRST', 'SECOND']))
        self.assertEqual(sorted(results), [200, 400])
        self.assertEqual(User.objects.filter(connected_user=self.owner).count(), 1)


@override_settings(APP_URL='https://club.example.test', WHITELABEL_NAME='Old Brand', IS_WHITELABEL=True)
class AccountEmailBrandingTests(TestCase):
    def setUp(self):
        self.config = InstanceConfiguration.objects.create(domain='club.example.test', name='Our Club')
        self.user = User.objects.create_user(username='EMAIL', email='email@example.test', first_name='Test')

    def test_all_account_templates_use_runtime_brand_and_absolute_logo(self):
        from django.template.loader import render_to_string
        from instance.email_branding import email_branding
        for logo in ['', '/api/instance/logo.png', 'https://assets.example.test/custom.png']:
            self.config.logo_path = logo
            self.config.save()
            context = email_branding()
            for template in ['email_welcome_collaborator_message', 'email_welcome_message', 'email_welcome_password_message']:
                with self.subTest(logo=logo, template=template):
                    html = render_to_string('email/account/' + template + '.html', context)
                    self.assertIn('Our Club', html)
                    self.assertNotIn('bakney.com', html)
                    self.assertNotIn('Old Brand', html)
                    expected = 'https://assets.example.test/custom.png' if logo.startswith('https') else 'https://club.example.test' + (logo or '/oem/assozeta/brand/logo.svg')
                    self.assertIn(expected, html)
                    if logo == '/api/instance/logo.png':
                        self.assertIn('?v=', html)

    @patch('application.views.auth_views.send_mail_async.apply_async')
    def test_welcome_sender_passes_branding(self, send):
        AuthUtils.send_welcome_email(self.user)
        args = send.call_args.kwargs['kwargs']
        self.assertEqual(args['subject'], 'Our Club | Benvenuto')
        self.assertIn('https://club.example.test/oem/assozeta/brand/logo.svg', args['html_message'])

    @patch('application.views.collaborator_views.send_mail_async.apply_async')
    def test_invitation_sender_passes_branding_and_token_link(self, send):
        from application.tests.fixtures.factories import create_test_billing_subscription
        self.user.role = User.ASSOCIATION
        self.user.save()
        association = SportAssociation.objects.create(user=self.user, denomination='Our Club', tax_code='12345678901')
        self.config.primary_association = association
        self.config.save()
        create_test_billing_subscription(user=self.user, plan_type='pro')
        client = APIClient()
        client.force_authenticate(user=self.user)
        response = client.post('/collaborators/add', {'email': 'new@example.test'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        invite = CollaborationInvites.objects.get(email='new@example.test')
        args = send.call_args.kwargs['kwargs']
        self.assertEqual(args['subject'], 'Our Club | Invito a collaborare')
        self.assertIn('https://club.example.test/#/invite/' + invite.token, args['html_message'])
        self.assertIn('https://club.example.test/oem/assozeta/brand/logo.svg', args['html_message'])
        self.assertNotIn('bakney.com', args['html_message'])
