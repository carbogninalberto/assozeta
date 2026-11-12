from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from application.models.user_models import CollaborationInvites


@override_settings(DEBUG=True)
class SignupPolicyTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _payload(self, **overrides):
        payload = {
            'first_name': 'Mario',
            'last_name': 'Rossi',
            'username': 'mario.rossi',
            'email': 'mario.rossi@example.com',
            'password': 'StrongPass!1',
            'sport_association': False,
        }
        payload.update(overrides)
        return payload

    def test_association_signup_is_rejected_without_side_effects(self):
        response = self.client.post(
            '/oauth2/signup',
            self._payload(
                sport_association=True,
                denomination='New Association',
                tax_code='12345678901',
            ),
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn('sport_association', response.data['details'])
        self.assertFalse(User.objects.exists())
        self.assertFalse(SportAssociation.objects.exists())

    def test_social_association_signup_is_rejected_before_token_verification(self):
        with patch(
            'application.views.auth_views.SocialAuthService.verify_google_token'
        ) as verify_google_token:
            response = self.client.post(
                '/oauth2/signup',
                self._payload(
                    sport_association=True,
                    token='social-token',
                    backend='google-oauth2',
                ),
                format='json',
            )

        self.assertEqual(response.status_code, 403)
        verify_google_token.assert_not_called()
        self.assertFalse(User.objects.exists())

    def test_signup_requires_a_boolean_account_discriminator(self):
        missing_payload = self._payload()
        missing_payload.pop('sport_association')

        missing_response = self.client.post('/oauth2/signup', missing_payload, format='json')
        string_response = self.client.post(
            '/oauth2/signup',
            self._payload(sport_association='false'),
            format='json',
        )

        self.assertEqual(missing_response.status_code, 400)
        self.assertEqual(string_response.status_code, 400)
        self.assertFalse(User.objects.exists())

    @patch('application.views.auth_views.AuthUtils.send_welcome_email')
    @patch('application.views.auth_views.JWTTokenService.build_login_response')
    @patch('application.views.auth_views.JWTTokenService.generate_tokens_for_user')
    def test_athlete_signup_remains_available(
        self,
        generate_tokens,
        build_login_response,
        send_welcome_email,
    ):
        generate_tokens.return_value = {
            'access_token': 'access-token',
            'refresh_token': 'refresh-token',
        }
        build_login_response.return_value = {
            'access_token': 'access-token',
            'user_data': {},
        }

        response = self.client.post('/oauth2/signup', self._payload(), format='json')

        self.assertEqual(response.status_code, 200, response.content)
        athlete = User.objects.get(username='MARIO.ROSSI')
        self.assertEqual(athlete.role, User.ATHLETE)
        self.assertFalse(SportAssociation.objects.exists())
        send_welcome_email.assert_called_once_with(athlete)

    @patch('application.views.auth_views.AuthUtils.send_welcome_email')
    @patch('application.views.auth_views.JWTTokenService.build_login_response')
    @patch('application.views.auth_views.JWTTokenService.generate_tokens_for_user')
    def test_invited_collaborator_signup_remains_available(
        self,
        generate_tokens,
        build_login_response,
        send_welcome_email,
    ):
        owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='StrongPass!1',
            role=User.ASSOCIATION,
        )
        association = SportAssociation.objects.create(
            user=owner,
            denomination='Primary Association',
            tax_code='12345678901',
        )
        invite = CollaborationInvites.objects.create(
            user=owner,
            email='collaborator@example.com',
            expiration_date=timezone.now() + timedelta(days=1),
            token='invite-token',
        )
        generate_tokens.return_value = {
            'access_token': 'access-token',
            'refresh_token': 'refresh-token',
        }
        build_login_response.return_value = {
            'access_token': 'access-token',
            'user_data': {},
        }

        response = self.client.post(
            '/oauth2/signup',
            self._payload(
                username='collaborator',
                email='collaborator@example.com',
                collaboratorToken=invite.token,
            ),
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        collaborator = User.objects.get(username='COLLABORATOR')
        self.assertEqual(collaborator.role, User.COLLABORATOR)
        self.assertEqual(collaborator.connected_user, owner)
        self.assertEqual(SportAssociation.objects.get(), association)
        self.assertFalse(CollaborationInvites.objects.filter(pk=invite.pk).exists())
        send_welcome_email.assert_called_once_with(collaborator)


GOOGLE = 'application.views.auth_views.SocialAuthService.verify_google_token'
APPLE = 'application.views.auth_views.SocialAuthService.verify_apple_token'


@override_settings(DEBUG=True)
class SocialSignupTests(TestCase):
    """Social signup requires a verified e-mail and leaves existing accounts unchanged."""

    def setUp(self):
        self.client = APIClient()
        patches = [
            patch('application.views.auth_views.AuthUtils.send_welcome_email'),
            patch('application.views.auth_views.JWTTokenService.build_login_response',
                  side_effect=lambda user, tokens: {'access_token': tokens['access_token'],
                                                    'user_data': {'user_id': str(user.user_id)}}),
            patch('application.views.auth_views.JWTTokenService.generate_tokens_for_user',
                  return_value={'access_token': 'access-token', 'refresh_token': 'refresh-token'}),
        ]
        self.send_welcome_email, self.build_login_response, self.generate_tokens = [item.start() for item in patches]
        for item in patches:
            self.addCleanup(item.stop)

    def _info(self, email='new.person@example.com', **overrides):
        info = {'email': email, 'email_verified': True, 'first_name': 'Ada', 'last_name': 'Nuova', 'sub': 'subject'}
        info.update(overrides)
        return {key: value for key, value in info.items() if value is not ...}

    def _signup(self, info, *, provider=GOOGLE, backend='google-oauth2', **overrides):
        payload = {'username': 'new.person', 'email': 'new.person@example.com', 'first_name': 'ada', 'last_name': 'nuova',
                   'sport_association': False, 'token': 'social-token', 'backend': backend}
        payload.update(overrides)
        with patch(provider, return_value=(True, info)):
            return self.client.post('/oauth2/signup', payload, format='json')

    def _owner(self, **overrides):
        values = {'username': 'OWNER', 'email': 'owner@example.com', 'password': 'StrongPass!1', 'role': User.ASSOCIATION,
                  'first_name': 'Olga', 'last_name': 'Owner'}
        values.update(overrides)
        owner = User.objects.create_user(**values)
        SportAssociation.objects.create(user=owner, denomination='Primary Association', tax_code='12345678901')
        return owner

    def _invite(self, owner, **overrides):
        values = {'user': owner, 'email': 'new.person@example.com', 'token': 'invite-token',
                  'expiration_date': timezone.now() + timedelta(days=1),
                  'collaborator_role': User.CUSTOM_COLLABORATOR_ROLE, 'collaborator_permissions': ['association.members.read']}
        values.update(overrides)
        return CollaborationInvites.objects.create(**values)

    def _stored(self, user):
        return User.original_objects.filter(pk=user.pk).values().get()

    def assert_refused(self, response):
        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(response.data, {'error': 'Invalid social token'})
        self.generate_tokens.assert_not_called()
        self.send_welcome_email.assert_not_called()

    def test_verified_google_signup_creates_an_athlete(self):
        response = self._signup(self._info())

        self.assertEqual(response.status_code, 200, response.content)
        user = User.objects.get(username='NEW.PERSON')
        self.assertEqual((user.email, user.first_name, user.last_name, user.role, user.connected_user),
                         ('new.person@example.com', 'Ada', 'Nuova', User.ATHLETE, None))
        self.assertFalse(user.has_usable_password())
        self.send_welcome_email.assert_called_once_with(user)

    def test_google_signup_requires_a_verified_email(self):
        for value in (False, ..., None, 'false', 'True', 1, 'yes'):
            with self.subTest(email_verified=value):
                self.assert_refused(self._signup(self._info(email_verified=value)))
        self.assertFalse(User.objects.exists())

    def test_signup_requires_an_email_in_the_identity(self):
        for value in (None, '', 17, ...):
            with self.subTest(email=value):
                self.assert_refused(self._signup(self._info(email=value)))
        self.assertFalse(User.objects.exists())

    def test_apple_signup_accepts_the_string_and_boolean_claim(self):
        for index, value in enumerate(('true', True)):
            with self.subTest(email_verified=value):
                response = self._signup(self._info(email=f'apple{index}@example.com', email_verified=value, first_name='', last_name=''),
                                        provider=APPLE, backend='apple-id', username=f'apple{index}')
                self.assertEqual(response.status_code, 200, response.content)
                user = User.objects.get(username=f'APPLE{index}')
                self.assertEqual((user.email, user.first_name, user.last_name), (f'apple{index}@example.com', 'ada', 'nuova'))
        self.generate_tokens.reset_mock()
        self.send_welcome_email.reset_mock()
        for value in ('false', False, ..., 'TRUE'):
            with self.subTest(email_verified=value):
                self.assert_refused(self._signup(self._info(email='apple9@example.com', email_verified=value),
                                                 provider=APPLE, backend='apple', username='apple9'))
        self.assertEqual(User.objects.count(), 2)

    def test_apple_private_relay_address_is_the_account_email(self):
        relay = 'abc123xyz@privaterelay.appleid.com'
        response = self._signup(self._info(email=relay, email_verified='true', is_private_email='true', first_name='', last_name=''),
                                provider=APPLE, backend='apple-id')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(User.objects.get(username='NEW.PERSON').email, relay)

    def test_unverified_email_of_an_existing_account_is_refused(self):
        owner = self._owner()
        before = self._stored(owner)

        self.assert_refused(self._signup(self._info(email='Owner@Example.com', email_verified=False)))
        self.assert_refused(self._signup(self._info(email='Owner@Example.com', email_verified=False), collaboratorToken='x'))
        self.assertEqual(self._stored(owner), before)
        self.assertEqual(User.objects.count(), 1)

    def test_existing_account_signs_in_unchanged(self):
        owner = self._owner()
        invite = self._invite(owner, email='owner@example.com')
        before = self._stored(owner)

        for extra in ({}, {'collaboratorToken': invite.token}, {'collaboratorToken': 'unknown-token'}):
            with self.subTest(extra=extra):
                response = self._signup(self._info(email='OWNER@example.com', first_name='Other', last_name='Name'),
                                        password='Another-Pass1!', first_name='x', last_name='y', **extra)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.data['user_data']['user_id'], str(owner.user_id))
                self.assertEqual(self._stored(owner), before)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(CollaborationInvites.objects.filter(pk=invite.pk).exists())
        self.send_welcome_email.assert_not_called()

    def test_existing_account_follows_the_login_rules(self):
        owner = self._owner()
        info = self._info(email='owner@example.com')

        User.objects.filter(pk=owner.pk).update(is_active=False)
        response = self._signup(info)
        self.assertEqual((response.status_code, response.data), (401, {'error': 'invalid credentials'}))

        User.objects.filter(pk=owner.pk).update(is_active=True, two_fa=True, two_fa_secret='JBSWY3DPEHPK3PXP')
        response = self._signup(info)
        self.assertEqual((response.status_code, response.data), (401, {'msg': 'OTP code required.'}))
        response = self._signup(info, otp='000000')
        self.assertEqual((response.status_code, response.data), (401, {'msg': 'OTP code not valid.'}))
        import pyotp
        response = self._signup(info, otp=pyotp.TOTP('JBSWY3DPEHPK3PXP').now())
        self.assertEqual(response.status_code, 200, response.content)

        User.objects.filter(pk=owner.pk).update(two_fa=False)
        User.objects.create_user(username='SECOND', email='OWNER@example.com', password='StrongPass!1', role=User.ATHLETE)
        self.generate_tokens.reset_mock()
        response = self._signup(info)
        self.assertEqual((response.status_code, response.data), (401, {'error': 'invalid credentials'}))
        self.generate_tokens.assert_not_called()
        self.send_welcome_email.assert_not_called()

    def test_collaborator_signup_needs_its_invite(self):
        owner = self._owner()
        expired = self._invite(owner, token='expired-token', expiration_date=timezone.now() - timedelta(days=1))

        response = self._signup(self._info(), collaboratorToken='unknown-token')
        self.assertEqual((response.status_code, response.data), (400, {'msg': 'Collaboration invite not found.'}))
        response = self._signup(self._info(), collaboratorToken=expired.token)
        self.assertEqual((response.status_code, response.data), (400, {'msg': 'Collaboration invite expired.'}))
        self.assertEqual(User.objects.count(), 1)
        self.generate_tokens.assert_not_called()

    def test_invited_collaborator_signup_uses_the_invite(self):
        owner = self._owner()
        invite = self._invite(owner)

        response = self._signup(self._info(), collaboratorToken=invite.token)

        self.assertEqual(response.status_code, 200, response.content)
        collaborator = User.objects.get(username='NEW.PERSON')
        self.assertEqual((collaborator.role, collaborator.connected_user, collaborator.collaborator_role,
                          collaborator.collaborator_permissions),
                         (User.COLLABORATOR, owner, User.CUSTOM_COLLABORATOR_ROLE, ['association.members.read']))
        self.assertFalse(CollaborationInvites.objects.filter(pk=invite.pk).exists())
        self.assertEqual(self._stored(owner)['role'], User.ASSOCIATION)
