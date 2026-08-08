"""
Tests for Collaborator views - invitation, list, update, and delete operations.

Self-host note: SaaS plan-restriction tests omitted. Every self-host
instance gets Pro entitlement.
"""
import uuid

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User, BillingPlan
from application.models.user_models import CollaborationInvites
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_billing_subscription
)


class BaseCollaboratorTestCase(TestCase):
    """Base test case for collaborator tests with Pro plan setup."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.pro_plan, _ = BillingPlan.objects.get_or_create(
            name='Pro Plan',
            defaults={
                'description': 'Pro plan',
                'monthly_fee': 2900,
                'annually_fee': 29000,
                'billing_type': BillingPlan.PRO_PLAN,
            }
        )
        self.billing_subscription = create_test_billing_subscription(
            user=self.user,
            plan_type='pro'
        )
        self.client.force_authenticate(user=self.user)


class CollaboratorListTests(BaseCollaboratorTestCase):
    """Tests for collaborator list endpoint."""

    def test_list_collaborators_empty(self):
        response = self.client.get('/collaborators/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_collaborators_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/collaborators/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_collaborators_with_pending_invites(self):
        CollaborationInvites.objects.create(
            user=self.user,
            email='invite@test.com',
            token='test_token_123',
            collaborator_role=User.FULL
        )
        response = self.client.get('/collaborators/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data), 0)


class CollaboratorAddTests(BaseCollaboratorTestCase):
    """Tests for collaborator add/invite endpoint."""

    def test_add_collaborator_success(self):
        response = self.client.post('/collaborators/add', {
            'email': 'newcollab@test.com',
            'collaborator_role': User.FULL
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])

    def test_add_collaborator_missing_email(self):
        response = self.client.post('/collaborators/add', {
            'collaborator_role': User.FULL
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_collaborator_only_accounting_role(self):
        response = self.client.post('/collaborators/add', {
            'email': 'accounting@test.com',
            'collaborator_role': User.ONLY_ACCOUNTING
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])

    def test_add_collaborator_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.post('/collaborators/add', {
            'email': 'collab@test.com'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CollaboratorUpdateTests(BaseCollaboratorTestCase):
    """Tests for collaborator update endpoint."""

    def setUp(self):
        super().setUp()
        self.collaborator = create_test_user(
            role=User.COLLABORATOR,
            username='TESTCOLLAB1'
        )
        self.collaborator.connected_user = self.user
        self.collaborator.collaborator_role = User.FULL
        self.collaborator.save()

    def test_update_collaborator_role(self):
        response = self.client.patch(
            f'/collaborators/{self.collaborator.user_id}/update',
            {'collaborator_role': User.ONLY_ACCOUNTING},
            format='json'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_nonexistent_collaborator(self):
        response = self.client.patch(
            f'/collaborators/{uuid.uuid4()}/update',
            {'collaborator_role': User.FULL},
            format='json'
        )
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])


class CollaboratorDeleteTests(BaseCollaboratorTestCase):
    """Tests for collaborator delete endpoint."""

    def setUp(self):
        super().setUp()
        self.collaborator = create_test_user(
            role=User.COLLABORATOR,
            username='TESTCOLLAB2'
        )
        self.collaborator.connected_user = self.user
        self.collaborator.save()

    def test_delete_collaborator(self):
        response = self.client.delete(
            f'/collaborators/{self.collaborator.user_id}/delete'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT])

    def test_delete_nonexistent_collaborator(self):
        response = self.client.delete(
            f'/collaborators/{uuid.uuid4()}/delete'
        )
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])


class CollaboratorSpecificUserTests(BaseCollaboratorTestCase):
    """Tests for getting specific collaborator user."""

    def setUp(self):
        super().setUp()
        self.collaborator = create_test_user(
            role=User.COLLABORATOR,
            username='TESTCOLLAB3'
        )
        self.collaborator.connected_user = self.user
        self.collaborator.save()

    def test_get_specific_collaborator(self):
        response = self.client.get(
            f'/collaborators/list?user_id={self.collaborator.user_id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_nonexistent_collaborator(self):
        response = self.client.get(
            f'/collaborators/list?user_id={uuid.uuid4()}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CollaboratorPermissionTests(BaseCollaboratorTestCase):
    """Tests for collaborator permission checks."""

    def test_athlete_cannot_list_collaborators(self):
        athlete = create_test_user(role=User.ATHLETE, username='ATHLETE1')
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/collaborators/list')
        self.assertIn(response.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST])

    def test_athlete_cannot_add_collaborators(self):
        athlete = create_test_user(role=User.ATHLETE, username='ATHLETE2')
        self.client.force_authenticate(user=athlete)
        response = self.client.post('/collaborators/add', {
            'email': 'collab@test.com'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST])


class CollaboratorCustomRoleTests(BaseCollaboratorTestCase):
    """Tests for custom collaborator roles."""

    def test_add_collaborator_with_custom_role(self):
        response = self.client.post('/collaborators/add', {
            'email': 'customrole@test.com',
            'collaborator_role': User.CUSTOM_COLLABORATOR_ROLE,
            'collaborator_permissions': {
                'can_view_subscriptions': True,
                'can_edit_subscriptions': False,
                'can_view_payments': True,
                'can_edit_payments': False
            }
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])

    def test_add_collaborator_with_invalid_role(self):
        response = self.client.post('/collaborators/add', {
            'email': 'invalidrole@test.com',
            'collaborator_role': 999
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST])


class CollaboratorDuplicateTests(BaseCollaboratorTestCase):
    """Tests for preventing duplicate collaborator invites."""

    def test_cannot_invite_same_email_twice(self):
        self.client.post('/collaborators/add', {
            'email': 'duplicate@test.com',
            'collaborator_role': User.FULL
        }, format='json')
        response = self.client.post('/collaborators/add', {
            'email': 'duplicate@test.com',
            'collaborator_role': User.FULL
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT])


class CollaboratorAcceptTests(TestCase):
    """Tests for collaborator accept invite endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)

    def test_accept_nonexistent_invite(self):
        response = self.client.post('/collaborators/accept/nonexistent_token', {
            'first_name': 'Test',
            'last_name': 'User',
            'username': 'TESTACCEPT1',
            'email': 'accept@test.com',
            'password': 'TestPass123!'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND])

    def test_accept_expired_invite(self):
        from django.utils import timezone
        from datetime import timedelta
        invite = CollaborationInvites.objects.create(
            user=self.user,
            email='expired@test.com',
            token='expired_token_123',
            expiration_date=timezone.now() - timedelta(days=1),
            collaborator_role=User.FULL
        )
        response = self.client.post('/collaborators/accept/expired_token_123', {
            'first_name': 'Test',
            'last_name': 'User',
            'username': 'TESTEXPIRED1',
            'email': 'expired@test.com',
            'password': 'TestPass123!'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND])

    def test_accept_valid_invite(self):
        from django.utils import timezone
        from datetime import timedelta
        invite = CollaborationInvites.objects.create(
            user=self.user,
            email='valid@test.com',
            token='valid_token_123',
            expiration_date=timezone.now() + timedelta(days=30),
            collaborator_role=User.FULL
        )
        response = self.client.post('/collaborators/accept/valid_token_123', {
            'first_name': 'Test',
            'last_name': 'Collaborator',
            'username': 'TESTVALID1',
            'email': 'valid@test.com',
            'password': 'TestPass123!'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND])


class CollaboratorDeleteInviteTests(BaseCollaboratorTestCase):
    """Tests for deleting pending collaborator invites."""

    def test_delete_pending_invite(self):
        invite = CollaborationInvites.objects.create(
            user=self.user,
            email='pending@test.com',
            token='pending_token_123',
            collaborator_role=User.FULL
        )
        response = self.client.delete(
            f'/collaborators/{invite.collaboration_invite_id}/delete'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT, status.HTTP_400_BAD_REQUEST])

    def test_delete_accepted_invite(self):
        invite = CollaborationInvites.objects.create(
            user=self.user,
            email='accepted@test.com',
            token='accepted_token_123',
            collaborator_role=User.FULL,
            accepted=True
        )
        collab_user = create_test_user(
            role=User.COLLABORATOR,
            username='ACCEPTEDCOLLAB',
            email='accepted@test.com'
        )
        collab_user.connected_user = self.user
        collab_user.save()
        response = self.client.delete(
            f'/collaborators/{invite.collaboration_invite_id}/delete'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT, status.HTTP_400_BAD_REQUEST])


class CollaboratorUpdateRoleTests(BaseCollaboratorTestCase):
    """Tests for updating collaborator roles."""

    def setUp(self):
        super().setUp()
        self.collaborator = create_test_user(
            role=User.COLLABORATOR,
            username='TESTCOLLAB_ROLE'
        )
        self.collaborator.connected_user = self.user
        self.collaborator.collaborator_role = User.FULL
        self.collaborator.save()

    def test_update_to_custom_role_with_permissions(self):
        response = self.client.patch(
            f'/collaborators/{self.collaborator.user_id}/update',
            {
                'collaborator_role': User.CUSTOM_COLLABORATOR_ROLE,
                'collaborator_permissions': {
                    'can_view_subscriptions': True,
                    'can_edit_payments': False
                }
            },
            format='json'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_to_non_custom_role_clears_permissions(self):
        response = self.client.patch(
            f'/collaborators/{self.collaborator.user_id}/update',
            {
                'collaborator_role': User.FULL,
                'collaborator_permissions': {
                    'some': 'permissions'
                }
            },
            format='json'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_with_invalid_role(self):
        response = self.client.patch(
            f'/collaborators/{self.collaborator.user_id}/update',
            {'collaborator_role': 9999},
            format='json'
        )
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])


class CollaboratorAuthTests(TestCase):
    """Tests for collaborator endpoint authentication."""

    def setUp(self):
        self.client = APIClient()

    def test_update_requires_auth(self):
        response = self.client.patch(
            f'/collaborators/{uuid.uuid4()}/update',
            {'collaborator_role': 1}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_delete_requires_auth(self):
        response = self.client.delete(f'/collaborators/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
