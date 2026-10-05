"""Token minting requires an association; token-authorized card reading stays public."""
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from application.models import User, SubscriptionToken
from application.tests.base import AuditlogDisabledMixin
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_associate,
    create_test_subscription, create_test_subscription_token,
)
from application.views.subscriptions_views import subscription_card


class ManualeCardAccessTests(AuditlogDisabledMixin, TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        person = create_test_associate(sport_association=self.association, user=self.owner)
        self.subscription = create_test_subscription(sport_association=self.association,
            associate=person, user=self.owner)
        other_owner = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_owner)
        other_person = create_test_associate(sport_association=other_association, user=other_owner)
        self.foreign = create_test_subscription(sport_association=other_association,
            associate=other_person, user=other_owner)
        self.reader = create_test_user(role=User.COLLABORATOR, connected_user=self.owner,
            collaborator_role=User.CUSTOM_COLLABORATOR_ROLE, collaborator_permissions=['association.members.read'])

    def test_member_details_include_current_card_configuration(self):
        from application.models import SportAssociationMembershipCardConfiguration
        from application.serializers.subscriptions_serializers import SubscriptionInfoSerializer
        config, _ = SportAssociationMembershipCardConfiguration.objects.update_or_create(
            sport_association=self.association, defaults={'customized_template': {
                'template': 'classic', 'show_qr_code': True, 'color': '#2255aa'}})
        serializer = SubscriptionInfoSerializer()
        self.assertEqual(serializer.get_membership_card_configuration(self.subscription)['customized_template'],
                         config.customized_template)
        config.customized_template['show_qr_code'] = False
        config.save(update_fields=['customized_template'])
        self.assertFalse(serializer.get_membership_card_configuration(self.subscription)['customized_template']['show_qr_code'])

    def mint(self, user=None, subscription=None):
        target = subscription or self.subscription
        request = self.factory.post(f'/api/subscription/{target.pk}/card', {}, format='json')
        if user: force_authenticate(request, user=user)
        return subscription_card(request, uid=str(target.pk))

    def read(self, token, user=None, subscription=None):
        target = subscription or self.subscription
        request = self.factory.get(f'/api/subscription/{target.pk}/card', {'token': str(token)})
        if user: force_authenticate(request, user=user)
        return subscription_card(request, uid=str(target.pk))

    def test_anonymous_cannot_mint_and_no_token_is_created(self):
        self.assertEqual(self.mint().status_code, 403)
        self.assertFalse(SubscriptionToken.objects.exists())

    def test_owner_cannot_mint_for_foreign_association(self):
        self.assertEqual(self.mint(self.owner, self.foreign).status_code, 404)
        self.assertFalse(SubscriptionToken.objects.exists())

    def test_owner_token_is_bound_to_subscription_and_expires_in_three_days(self):
        before = timezone.now()
        response = self.mint(self.owner)
        self.assertEqual(response.status_code, 200)
        token = SubscriptionToken.objects.get(token=response.data['token'])
        self.assertEqual(token.subscription_id, self.subscription.pk)
        self.assertGreaterEqual(token.expiration_date, before + timedelta(days=3))
        self.assertLessEqual(token.expiration_date, timezone.now() + timedelta(days=3))
        public = self.read(token.token)
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.data['member']['associate']['first_name'], self.subscription.associate.first_name)
        self.assertEqual(self.read(token.token, subscription=self.foreign).status_code, 400)

    def test_reader_can_read_valid_token_but_cannot_mint(self):
        token = create_test_subscription_token(subscription=self.subscription)
        self.assertEqual(self.read(token.token, self.reader).status_code, 200)
        self.assertEqual(self.mint(self.reader).status_code, 403)
        self.assertEqual(SubscriptionToken.objects.count(), 1)

    def test_collaborator_with_update_permission_can_mint_only_in_connected_association(self):
        editor = create_test_user(role=User.COLLABORATOR, connected_user=self.owner,
            collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.members.read', 'association.members.update'])
        self.assertEqual(self.mint(editor).status_code, 200)
        self.assertEqual(self.mint(editor, self.foreign).status_code, 404)
        self.assertEqual(SubscriptionToken.objects.count(), 1)

    def test_expired_token_is_rejected_and_removed(self):
        token = create_test_subscription_token(subscription=self.subscription,
            expiration_date=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.read(token.token).status_code, 400)
        self.assertFalse(SubscriptionToken.objects.filter(pk=token.pk).exists())
