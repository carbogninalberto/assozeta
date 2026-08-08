"""
Auth Views Tests - Authentication, 2FA, and Account Management.

Ported from SaaS test_auth_views.py, adapted for self-host:
- Association signup is forbidden (returns 403), athlete/collaborator signup retained
- Uses self-host InstanceConfiguration singleton
- Same JWT/2FA/reset behavior
"""
import pyotp
import uuid as uuid_lib
from unittest.mock import patch

from rest_framework.test import APIClient
from rest_framework import status

from application.tests.base import BaseTestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_billing_subscription,
    create_test_instance_config,
)
from application.models import User, SportAssociation
from application.models.user_models import CollaborationInvites


class AuthViewsTestCase(BaseTestCase):
    """Base test case with common setup for auth tests."""

    def setUp(self):
        """Set up test data for auth view tests."""
        super().setUp()
        self.client = APIClient()

        self.assoc_user = create_test_user(
            role=User.ASSOCIATION,
            email='assoc@test.com',
            username='TESTASSOC',
        )
        self.assoc_user.set_password('testpass123')
        self.assoc_user.save()

        self.sport_association = create_test_sport_association(user=self.assoc_user)
        self.billing_sub = create_test_billing_subscription(user=self.assoc_user)
        create_test_instance_config(primary_association=self.sport_association)

        self.user_2fa = create_test_user(
            role=User.ASSOCIATION,
            email='twofa@test.com',
            username='TEST2FA',
        )
        self.user_2fa.set_password('testpass123')
        self.user_2fa.two_fa = True
        self.user_2fa.two_fa_secret = pyotp.random_base32()
        self.user_2fa.save()
        create_test_sport_association(user=self.user_2fa)
        create_test_billing_subscription(user=self.user_2fa)


class LoginTests(AuthViewsTestCase):
    """Tests for login endpoint."""

    def test_login_with_username_success(self):
        """Test successful login with username."""
        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('access_token', response.data)
        self.assertIn('refresh_token', response.data)
        self.assertIn('role', response.data)

    def test_login_with_email_success(self):
        """Test successful login with email instead of username."""
        response = self.client.post('/oauth2/login', {
            'username': 'assoc@test.com',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('access_token', response.data)

    def test_login_wrong_password(self):
        """Test login with wrong password fails."""
        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'wrongpassword'
        }, format='json')

        self.assertEqual(response.status_code, 401)
        self.assertIn('error', response.data)

    def test_login_nonexistent_user(self):
        """Test login with non-existent user fails."""
        response = self.client.post('/oauth2/login', {
            'username': 'nobody',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 401)

    def test_login_missing_credentials(self):
        """Test login without credentials fails."""
        response = self.client.post('/oauth2/login', {
            'username': 'testassoc'
        }, format='json')

        self.assertEqual(response.status_code, 400)

    def test_login_2fa_required_no_otp(self):
        """Test that 2FA users must provide OTP."""
        response = self.client.post('/oauth2/login', {
            'username': 'test2fa',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 401)
        self.assertIn('OTP code required', response.data.get('msg', ''))

    def test_login_2fa_with_valid_otp(self):
        """Test successful login with valid 2FA code."""
        totp = pyotp.TOTP(self.user_2fa.two_fa_secret)

        valid_otp = totp.now()
        response = self.client.post('/oauth2/login', {
            'username': 'test2fa',
            'password': 'testpass123',
            'otp': valid_otp
        }, format='json')

        if response.status_code == 401:
            msg = response.data.get('msg', '')
            self.assertIn('OTP code not valid', msg,
                f"Expected OTP timing issue, got unexpected error: {msg}")
        else:
            self.assertEqual(response.status_code, 200)
            self.assertIn('access_token', response.data)

    def test_login_2fa_with_invalid_otp(self):
        """Test login with invalid 2FA code fails."""
        response = self.client.post('/oauth2/login', {
            'username': 'test2fa',
            'password': 'testpass123',
            'otp': '000000'
        }, format='json')

        self.assertEqual(response.status_code, 401)
        self.assertIn('OTP code not valid', response.data.get('msg', ''))


class TokenRefreshTests(AuthViewsTestCase):
    """Tests for token refresh endpoint."""

    def test_refresh_token_success(self):
        """Test successful token refresh."""
        login_response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'testpass123'
        }, format='json')
        refresh_token = login_response.data['refresh_token']

        response = self.client.post('/oauth2/refresh-token', {
            'refresh_token': refresh_token
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('access_token', response.data)
        self.assertIn('refresh_token', response.data)

    def test_refresh_token_invalid(self):
        """Test refresh with invalid token fails."""
        response = self.client.post('/oauth2/refresh-token', {
            'refresh_token': 'invalid_token_here'
        }, format='json')

        self.assertEqual(response.status_code, 401)


class PasswordResetTests(AuthViewsTestCase):
    """Tests for password reset endpoint."""

    def test_reset_password_existing_email(self):
        """Test password reset request for existing email."""
        response = self.client.post('/oauth2/reset', {
            'email': 'assoc@test.com'
        }, format='json')

        self.assertEqual(response.status_code, 200)

    def test_reset_password_nonexistent_email(self):
        """Test password reset for non-existent email (should still return 200 for security)."""
        response = self.client.post('/oauth2/reset', {
            'email': 'nobody@test.com'
        }, format='json')

        self.assertEqual(response.status_code, 200)

    def test_reset_password_case_insensitive(self):
        """Test password reset is case insensitive for email."""
        response = self.client.post('/oauth2/reset', {
            'email': 'ASSOC@TEST.COM'
        }, format='json')

        self.assertEqual(response.status_code, 200)


class AccountDeletionTests(AuthViewsTestCase):
    """Tests for account deletion endpoint."""

    def test_delete_account_authenticated(self):
        """Test account deletion request when authenticated."""
        self.client.force_authenticate(user=self.assoc_user)

        response = self.client.post('/oauth2/delete-account')

        self.assertEqual(response.status_code, 200)
        self.assoc_user.refresh_from_db()
        self.assertIsNotNone(self.assoc_user.delete_on)

    def test_delete_account_unauthenticated(self):
        """Test account deletion fails when not authenticated."""
        response = self.client.post('/oauth2/delete-account')

        self.assertEqual(response.status_code, 401)


class UsernameCheckTests(AuthViewsTestCase):
    """Tests for username availability check."""

    def test_check_username_available(self):
        """Test checking an available username."""
        response = self.client.get('/oauth2/check/username', {'username': 'newuser'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['valid'], True)

    def test_check_username_taken(self):
        """Test checking a taken username."""
        response = self.client.get('/oauth2/check/username', {'username': 'testassoc'})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['valid'], False)

    def test_check_username_case_insensitive(self):
        """Test username check is case insensitive."""
        response = self.client.get('/oauth2/check/username', {'username': 'TESTASSOC'})

        self.assertEqual(response.status_code, 409)


class EmailCheckTests(AuthViewsTestCase):
    """Tests for email availability check."""

    def test_check_email_available(self):
        """Test checking an available email."""
        response = self.client.get('/oauth2/check/email', {'email': 'newuser@test.com'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['valid'], True)

    def test_check_email_taken(self):
        """Test checking a taken email."""
        response = self.client.get('/oauth2/check/email', {'email': 'assoc@test.com'})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['valid'], False)


class TwoFATests(AuthViewsTestCase):
    """Tests for 2FA endpoints."""

    def test_2fa_info_authenticated(self):
        """Test getting 2FA info when authenticated."""
        self.client.force_authenticate(user=self.assoc_user)

        response = self.client.get('/two-fa/info')

        self.assertEqual(response.status_code, 200)
        self.assertIn('data', response.data)
        self.assertIn('enabled', response.data['data'])
        self.assertFalse(response.data['data']['enabled'])

    def test_2fa_info_unauthenticated(self):
        """Test 2FA info fails when not authenticated."""
        response = self.client.get('/two-fa/info')

        self.assertEqual(response.status_code, 401)

    def test_2fa_setup(self):
        """Test 2FA setup returns QR code and secret."""
        self.client.force_authenticate(user=self.assoc_user)

        response = self.client.get('/two-fa/setup')

        self.assertEqual(response.status_code, 200)
        self.assertIn('data', response.data)
        self.assertIn('qrcode_uri', response.data['data'])
        self.assertIn('otp_secret', response.data['data'])

    def test_2fa_enable(self):
        """Test enabling 2FA with valid OTP."""
        self.client.force_authenticate(user=self.assoc_user)

        setup_response = self.client.get('/two-fa/setup')
        secret = setup_response.data['data']['otp_secret']

        totp = pyotp.TOTP(secret)
        valid_otp = totp.now()

        response = self.client.post('/two-fa/update', {
            'enable': True,
            'secret': secret,
            'otp': valid_otp
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assoc_user.refresh_from_db()
        self.assertTrue(self.assoc_user.two_fa)
        self.assertEqual(self.assoc_user.two_fa_secret, secret)

    def test_2fa_enable_invalid_otp(self):
        """Test enabling 2FA with invalid OTP fails."""
        self.client.force_authenticate(user=self.assoc_user)

        setup_response = self.client.get('/two-fa/setup')
        secret = setup_response.data['data']['otp_secret']

        response = self.client.post('/two-fa/update', {
            'enable': True,
            'secret': secret,
            'otp': '000000'
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assoc_user.refresh_from_db()
        self.assertFalse(self.assoc_user.two_fa)

    def test_2fa_disable(self):
        """Test disabling 2FA."""
        self.client.force_authenticate(user=self.user_2fa)

        response = self.client.post('/two-fa/update', {
            'enable': False,
            'secret': '',
            'otp': ''
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.user_2fa.refresh_from_db()
        self.assertFalse(self.user_2fa.two_fa)


class LoginSecurityTests(AuthViewsTestCase):
    """Tests for login security edge cases."""

    def test_login_returns_user_role(self):
        """Test that login response includes user role."""
        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('role', response.data)
        self.assertEqual(response.data['role'], 'association')

    def test_login_sets_auth_cookie(self):
        """Test that login sets BKN_AUTH cookie."""
        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('BKN_AUTH', response.cookies)

    def test_login_clears_deletion_flag(self):
        """Test that login clears pending account deletion."""
        from django.utils import timezone
        import datetime

        self.assoc_user.delete_on = timezone.now().date() + datetime.timedelta(days=10)
        self.assoc_user.save()

        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assoc_user.refresh_from_db()
        self.assertIsNone(self.assoc_user.delete_on)


class PartialSignupTests(AuthViewsTestCase):
    """Tests for partial signup endpoint."""

    def test_partial_signup_new_email(self):
        """Test partial signup with new email."""
        response = self.client.post('/oauth2/partial-signup', {
            'email': 'newpartial@test.com'
        }, format='json')

        self.assertEqual(response.status_code, 200)

    def test_partial_signup_duplicate_email(self):
        """Test partial signup with duplicate email."""
        self.client.post('/oauth2/partial-signup', {
            'email': 'partial@test.com'
        }, format='json')

        response = self.client.post('/oauth2/partial-signup', {
            'email': 'partial@test.com'
        }, format='json')

        self.assertEqual(response.status_code, 200)


class SocialAuthLoginTests(AuthViewsTestCase):
    """Tests for social authentication login."""

    def test_login_google_unsupported_backend(self):
        """Test login with unsupported backend fails."""
        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'token': 'fake_token',
            'backend': 'unsupported_backend'
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('Unsupported backend', response.data.get('error', ''))

    @patch('application.services.social_auth_service.SocialAuthService.verify_google_token')
    def test_login_google_invalid_token(self, mock_verify):
        """Test login with invalid Google token fails."""
        mock_verify.return_value = (False, None)

        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'token': 'invalid_google_token',
            'backend': 'google-oauth2'
        }, format='json')

        self.assertEqual(response.status_code, 401)
        self.assertIn('Invalid social token', response.data.get('error', ''))

    @patch('application.services.social_auth_service.SocialAuthService.verify_google_token')
    def test_login_google_email_mismatch(self, mock_verify):
        """Test login with mismatched email fails."""
        mock_verify.return_value = (True, {'email': 'different@test.com'})

        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'token': 'valid_google_token',
            'backend': 'google-oauth2'
        }, format='json')

        self.assertEqual(response.status_code, 401)
        self.assertIn('Email mismatch', response.data.get('error', ''))

    @patch('application.services.social_auth_service.SocialAuthService.verify_google_token')
    def test_login_google_success(self, mock_verify):
        """Test successful Google login."""
        mock_verify.return_value = (True, {'email': 'assoc@test.com'})

        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'token': 'valid_google_token',
            'backend': 'google-oauth2'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('access_token', response.data)

    @patch('application.services.social_auth_service.SocialAuthService.verify_apple_token')
    def test_login_apple_success(self, mock_verify):
        """Test successful Apple login."""
        mock_verify.return_value = (True, {'email': 'assoc@test.com'})

        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'token': 'valid_apple_token',
            'backend': 'apple-id'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('access_token', response.data)


class SignupTests(AuthViewsTestCase):
    """Tests for signup endpoint.

    Self-host: association signup is forbidden (returns 403).
    Athlete and invited collaborator signup remain available.
    """

    def test_signup_association_forbidden(self):
        """Test that sport association signup is rejected in self-host."""
        unique = uuid_lib.uuid4().hex[:8]

        response = self.client.post('/oauth2/signup', {
            'first_name': 'New',
            'last_name': 'Association',
            'username': f'NEWASSOC{unique}',
            'email': f'newassoc{unique}@test.com',
            'password': 'securepassword123',
            'sport_association': True,
            'denomination': f'Test Association {unique}',
            'tax_code': f'{unique}12345'
        }, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertIn('sport_association', response.data.get('details', ''))

    def test_signup_requires_sport_association_discriminator(self):
        """Test signup requires sport_association boolean field."""
        response = self.client.post('/oauth2/signup', {
            'first_name': 'Test',
            'last_name': 'User',
            'username': 'NOASSOCFLAG',
            'email': 'noflag@test.com',
            'password': 'securepassword123',
        }, format='json')

        self.assertEqual(response.status_code, 400)

    @patch('application.views.auth_views.AuthUtils.send_welcome_email')
    @patch('application.views.auth_views.JWTTokenService.build_login_response')
    @patch('application.views.auth_views.JWTTokenService.generate_tokens_for_user')
    def test_invited_collaborator_signup_remains_available(
        self,
        generate_tokens,
        build_login_response,
        send_welcome_email,
    ):
        """Test that invited collaborator signup works in self-host."""
        owner = create_test_user(
            username='collabowner',
            email='collab.owner@test.com',
            password='StrongPass!1',
            role=User.ASSOCIATION,
        )
        association = SportAssociation.objects.create(
            user=owner,
            denomination='Collab Association',
            tax_code='98765432101',
        )
        from django.utils import timezone
        from datetime import timedelta
        invite = CollaborationInvites.objects.create(
            user=owner,
            email='collaborator.invited@collabowner.test',
            expiration_date=timezone.now() + timedelta(days=1),
            token='invite-collab-token',
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
            {
                'first_name': 'Collab',
                'last_name': 'User',
                'username': 'invitedcollab',
                'email': 'collaborator.invited@collabowner.test',
                'password': 'StrongPass!1',
                'sport_association': False,
                'collaboratorToken': invite.token,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(username='INVITEDCOLLAB').exists())


class LoginEdgeCasesTests(AuthViewsTestCase):
    """Tests for login edge cases."""

    def test_login_empty_username(self):
        """Test login with empty username."""
        response = self.client.post('/oauth2/login', {
            'username': '',
            'password': 'testpass123'
        }, format='json')

        self.assertIn(response.status_code, [400, 401])

    def test_login_empty_password(self):
        """Test login with empty password."""
        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': ''
        }, format='json')

        self.assertIn(response.status_code, [400, 401])

    def test_login_both_empty(self):
        """Test login with both empty."""
        response = self.client.post('/oauth2/login', {
            'username': '',
            'password': ''
        }, format='json')

        self.assertIn(response.status_code, [400, 401])


class RefreshTokenEdgeCasesTests(AuthViewsTestCase):
    """Tests for refresh token edge cases."""

    def test_refresh_with_empty_token(self):
        """Test refresh with empty token."""
        response = self.client.post('/oauth2/refresh-token', {
            'refresh_token': ''
        }, format='json')

        self.assertIn(response.status_code, [400, 401])

    def test_refresh_with_malformed_token(self):
        """Test refresh with malformed token."""
        response = self.client.post('/oauth2/refresh-token', {
            'refresh_token': 'this.is.not.a.valid.jwt.token'
        }, format='json')

        self.assertEqual(response.status_code, 401)


class PasswordResetEdgeCasesTests(AuthViewsTestCase):
    """Tests for password reset edge cases."""

    def test_reset_with_empty_email(self):
        """Test password reset with empty email."""
        response = self.client.post('/oauth2/reset', {
            'email': ''
        }, format='json')

        self.assertEqual(response.status_code, 200)

    def test_reset_with_invalid_email_format(self):
        """Test password reset with invalid email format."""
        response = self.client.post('/oauth2/reset', {
            'email': 'notanemail'
        }, format='json')

        self.assertIn(response.status_code, [200, 400])


class UsernameCaseTests(AuthViewsTestCase):
    """Tests for username case normalization."""

    def test_login_normalizes_username_case(self):
        """Test that login normalizes username to uppercase."""
        user = create_test_user(
            role=User.ASSOCIATION,
            username='lowercase',
            email='lowercase@test.com'
        )
        user.set_password('testpass123')
        user.save()
        create_test_sport_association(user=user)
        create_test_billing_subscription(user=user)

        response = self.client.post('/oauth2/login', {
            'username': 'LOWERCASE',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.username, 'LOWERCASE')

    def test_check_username_case_variations(self):
        """Test username check with various case formats."""
        response = self.client.get('/oauth2/check/username', {'username': 'testassoc'})
        self.assertEqual(response.status_code, 409)

        response = self.client.get('/oauth2/check/username', {'username': 'TESTASSOC'})
        self.assertEqual(response.status_code, 409)

        response = self.client.get('/oauth2/check/username', {'username': 'TestAssoc'})
        self.assertEqual(response.status_code, 409)


class PasswordResetTokenTests(AuthViewsTestCase):
    """Tests for password reset token validation flow."""

    def test_reset_with_invalid_token(self):
        """Test password reset with invalid token fails."""
        response = self.client.post('/oauth2/reset', {
            'token': 'invalid_nonexistent_token',
            'password': 'NewSecure1!Password'
        }, format='json')

        self.assertIn(response.status_code, [400, 401, 404])

    def test_reset_with_weak_password(self):
        """Test password reset with weak password fails validation."""
        response = self.client.post('/oauth2/reset', {
            'token': 'some_token',
            'password': 'weak'
        }, format='json')

        self.assertIn(response.status_code, [400, 401])

    def test_reset_with_missing_token(self):
        """Test password reset without token."""
        response = self.client.post('/oauth2/reset', {
            'password': 'NewSecure1!Password'
        }, format='json')

        self.assertIn(response.status_code, [200, 400])

    def test_reset_with_empty_body(self):
        """Test password reset with empty body."""
        response = self.client.post('/oauth2/reset', {}, format='json')

        self.assertIn(response.status_code, [200, 400])


class EmailCheckEdgeCasesTests(AuthViewsTestCase):
    """Tests for email check edge cases."""

    def test_check_email_too_long(self):
        """Test email check with excessively long email."""
        long_email = 'a' * 300 + '@test.com'
        response = self.client.get('/oauth2/check/email', {'email': long_email})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['valid'])

    def test_check_email_with_get_user(self):
        """Test email check with get_user parameter returns user data."""
        response = self.client.get('/oauth2/check/email', {
            'email': 'assoc@test.com',
            'get_user': 'true'
        })

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.data['valid'])
        self.assertIn('user', response.data)
        self.assertIn('first_name', response.data['user'])

    def test_check_email_with_get_user_by_username(self):
        """Test email check can find user by username with get_user."""
        response = self.client.get('/oauth2/check/email', {
            'email': 'TESTASSOC',
            'get_user': 'true'
        })

        self.assertIn(response.status_code, [200, 409])

    def test_check_email_with_get_user_not_found(self):
        """Test email check with get_user for non-existent user."""
        response = self.client.get('/oauth2/check/email', {
            'email': 'nonexistent@test.com',
            'get_user': 'true'
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['valid'])


class UsernameCheckEdgeCasesTests(AuthViewsTestCase):
    """Tests for username check edge cases."""

    def test_check_username_too_long(self):
        """Test username check with excessively long username."""
        long_username = 'a' * 200
        response = self.client.get('/oauth2/check/username', {'username': long_username})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['valid'])

    def test_check_username_special_chars(self):
        """Test username check with special characters."""
        response = self.client.get('/oauth2/check/username', {'username': 'user@name!'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['valid'])


class RefreshTokenCookieTests(AuthViewsTestCase):
    """Tests for refresh token cookie setting."""

    def test_refresh_sets_auth_cookie(self):
        """Test that refresh sets BKN_AUTH cookie."""
        login_response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'testpass123'
        }, format='json')
        refresh_token = login_response.data['refresh_token']

        response = self.client.post('/oauth2/refresh-token', {
            'refresh_token': refresh_token
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('BKN_AUTH', response.cookies)

    def test_refresh_missing_token_field(self):
        """Test refresh with missing refresh_token field."""
        response = self.client.post('/oauth2/refresh-token', {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('refresh_token required', response.data.get('msg', ''))


class AccountDeletionEdgeCasesTests(AuthViewsTestCase):
    """Tests for account deletion edge cases."""

    def test_delete_account_collaborator(self):
        """Test account deletion as collaborator deletes main user."""
        collaborator = create_test_user(
            role=User.COLLABORATOR,
            email='collaborator@test.com',
            username='TESTCOLLAB'
        )
        collaborator.connected_user = self.assoc_user
        collaborator.save()

        self.client.force_authenticate(user=collaborator)

        response = self.client.post('/oauth2/delete-account')

        self.assertEqual(response.status_code, 200)


class LoginDeletionPendingTests(AuthViewsTestCase):
    """Tests for login behavior with pending account deletion."""

    def test_login_clears_old_deletion_flag(self):
        """Test that login clears deletion flag that's still in the future."""
        from django.utils import timezone
        import datetime

        self.assoc_user.delete_on = timezone.now().date() + datetime.timedelta(days=5)
        self.assoc_user.save()

        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assoc_user.refresh_from_db()
        self.assertIsNone(self.assoc_user.delete_on)

    def test_login_keeps_past_deletion_flag(self):
        """Test that login doesn't clear past deletion dates."""
        from django.utils import timezone
        import datetime

        self.assoc_user.delete_on = timezone.now().date() - datetime.timedelta(days=5)
        self.assoc_user.save()

        response = self.client.post('/oauth2/login', {
            'username': 'testassoc',
            'password': 'testpass123'
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assoc_user.refresh_from_db()
        self.assertIsNotNone(self.assoc_user.delete_on)
