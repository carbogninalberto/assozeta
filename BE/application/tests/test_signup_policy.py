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
