from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from application.models import User


class DuplicateEmailLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username='owner@example.com',
            email='owner@example.com',
            password='owner-password',
            role=User.ASSOCIATION,
        )
        self.athlete = User.objects.create_user(
            username='athlete-account',
            email='OWNER@example.com',
            password='athlete-password',
            role=User.ATHLETE,
        )

    @patch('application.views.auth_views.JWTTokenService.build_login_response')
    @patch('application.views.auth_views.JWTTokenService.generate_tokens_for_user')
    def test_password_selects_matching_user_from_duplicate_email(
        self,
        generate_tokens,
        build_login_response,
    ):
        generate_tokens.return_value = {
            'access_token': 'access-token',
            'refresh_token': 'refresh-token',
        }
        build_login_response.return_value = {'access_token': 'access-token'}

        response = self.client.post(
            '/oauth2/login',
            {'username': 'owner@example.com', 'password': 'owner-password'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        generate_tokens.assert_called_once()
        self.assertEqual(generate_tokens.call_args.args[0].user_id, self.owner.user_id)

    @patch('application.views.auth_views.JWTTokenService.build_login_response')
    @patch('application.views.auth_views.JWTTokenService.generate_tokens_for_user')
    def test_password_can_select_nonpreferred_duplicate_email_account(
        self,
        generate_tokens,
        build_login_response,
    ):
        generate_tokens.return_value = {
            'access_token': 'access-token',
            'refresh_token': 'refresh-token',
        }
        build_login_response.return_value = {'access_token': 'access-token'}

        response = self.client.post(
            '/oauth2/login',
            {'username': 'owner@example.com', 'password': 'athlete-password'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(generate_tokens.call_args.args[0].user_id, self.athlete.user_id)

    def test_invalid_password_with_duplicate_email_returns_401(self):
        response = self.client.post(
            '/oauth2/login',
            {'username': 'owner@example.com', 'password': 'wrong-password'},
            format='json',
        )

        self.assertEqual(response.status_code, 401)


class DuplicateEmailPasswordResetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username='owner-account',
            email='shared@example.com',
            password='owner-password',
            role=User.ASSOCIATION,
        )
        self.instructor = User.objects.create_user(
            username='cristina-instructor',
            email='SHARED@example.com',
            password='instructor-password',
            role=User.COLLABORATOR,
        )

    @patch('application.views.auth_views.AuthUtils.send_reset_email')
    @patch('application.views.auth_views.redis.Redis')
    def test_reset_sends_a_distinct_link_for_every_matching_account(self, redis_class, send_reset_email):
        response = self.client.post(
            '/oauth2/reset',
            {'email': '  shared@example.com  '},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(redis_class.return_value.set.call_count, 2)
        self.assertEqual(send_reset_email.call_count, 2)
        reset_users = {call.args[0].user_id for call in send_reset_email.call_args_list}
        self.assertEqual(reset_users, {self.owner.user_id, self.instructor.user_id})
        redis_class.return_value.close.assert_called_once()
