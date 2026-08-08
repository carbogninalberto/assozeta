import uuid
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from application.models.subscriptions_models import Subscription, Tags
from application.models.user_models import User
from application.tests.fixtures.factories import (
    create_bulk_subscriptions,
    create_test_archived_subscription,
    create_test_associate,
    create_test_billing_subscription,
    create_test_payment,
    create_test_sport_association,
    create_test_subscription,
    create_test_tag,
    create_test_user,
)


class SubscriptionViewsTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(
            user=self.user,
            plan_type='pro',
        )
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.client.force_authenticate(user=self.user)

    def create_subscription(self, **kwargs):
        return create_test_subscription(
            sport_association=self.sport_association,
            **kwargs,
        )

    def subscription_payload(self, **associate_overrides):
        associate_data = {
            'is_minor': False,
            'first_name': 'Giovanni',
            'last_name': 'Barbieri',
            'sex': 'M',
            'tax_code': f'BRBGVN90B10{uuid.uuid4().hex[:4].upper()}T',
            'born_date': '10/02/1990',
            'born_city': 'Roma',
            'address': 'Via Roma 1',
            'address_city': 'Roma',
            'address_cap': '00100',
            'phone': '',
            'email': '',
        }
        associate_data.update(associate_overrides)
        return {
            'new_user_account': {
                'new_member': False,
                'new_member_info': {
                    'first_name': '',
                    'last_name': '',
                    'phone': '',
                    'email': '',
                },
            },
            'associate_data': associate_data,
            'associate_tutor_data': None,
            'medical_certificate': {'medical_id': None, 'filename': ''},
            'signature': {'there_is_signature': False, 'data': ''},
        }


class SubscriptionTagListTests(SubscriptionViewsTestCase):
    def test_list_tags(self):
        create_test_tag(sport_association=self.sport_association, tag_name='First')
        create_test_tag(sport_association=self.sport_association, tag_name='Second')

        response = self.client.get('/subscription/tags/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([tag['tag_name'] for tag in response.data['tags']], ['First', 'Second'])

    def test_list_tags_empty(self):
        response = self.client.get('/subscription/tags/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tags'], [])

    def test_list_only_own_tags(self):
        create_test_tag(sport_association=self.sport_association, tag_name='Mine')
        create_test_tag(sport_association=create_test_sport_association(), tag_name='Foreign')

        response = self.client.get('/subscription/tags/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([tag['tag_name'] for tag in response.data['tags']], ['Mine'])

    def test_list_tags_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get('/subscription/tags/list')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionTagMutationTests(SubscriptionViewsTestCase):
    def test_add_tag(self):
        response = self.client.post(
            '/subscription/tags/add',
            {'tag_name': 'New'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Tags.objects.filter(
            sport_association=self.sport_association,
            tag_name='New',
        ).exists())

    def test_add_tag_requires_name(self):
        for payload in ({}, {'tag_name': ''}, {'tag_name': '   '}):
            with self.subTest(payload=payload):
                response = self.client.post('/subscription/tags/add', payload, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_tag(self):
        tag = create_test_tag(sport_association=self.sport_association, tag_name='Old')

        response = self.client.patch(
            f'/subscription/tags/{tag.tag_id}/update',
            {'tag_name': 'Updated'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tag.refresh_from_db()
        self.assertEqual(tag.tag_name, 'Updated')

    def test_update_missing_or_foreign_tag_returns_not_found(self):
        foreign = create_test_tag(sport_association=create_test_sport_association())
        for tag_id in (uuid.uuid4(), foreign.tag_id):
            with self.subTest(tag_id=tag_id):
                response = self.client.patch(
                    f'/subscription/tags/{tag_id}/update',
                    {'tag_name': 'Updated'},
                    format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_tag_requires_name(self):
        tag = create_test_tag(sport_association=self.sport_association)

        response = self.client.patch(
            f'/subscription/tags/{tag.tag_id}/update',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_tag(self):
        tag = create_test_tag(sport_association=self.sport_association)

        response = self.client.delete(f'/subscription/tags/{tag.tag_id}/delete')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Tags.objects.filter(tag_id=tag.tag_id).exists())

    def test_delete_missing_or_foreign_tag_returns_not_found(self):
        foreign = create_test_tag(sport_association=create_test_sport_association())
        for tag_id in (uuid.uuid4(), foreign.tag_id):
            with self.subTest(tag_id=tag_id):
                response = self.client.delete(f'/subscription/tags/{tag_id}/delete')
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_tag_mutations_require_authentication(self):
        tag = create_test_tag(sport_association=self.sport_association)
        self.client.force_authenticate(user=None)

        responses = [
            self.client.post('/subscription/tags/add', {'tag_name': 'Denied'}, format='json'),
            self.client.patch(
                f'/subscription/tags/{tag.tag_id}/update',
                {'tag_name': 'Denied'},
                format='json',
            ),
            self.client.delete(f'/subscription/tags/{tag.tag_id}/delete'),
        ]

        for response in responses:
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionTagAssignmentTests(SubscriptionViewsTestCase):
    def setUp(self):
        super().setUp()
        self.tag = create_test_tag(sport_association=self.sport_association)
        self.subscription = self.create_subscription()

    def test_assign_and_unassign_tag(self):
        assign_response = self.client.patch(
            f'/subscription/tags/{self.tag.tag_id}/assign/{self.subscription.subscription_id}'
        )
        self.assertEqual(assign_response.status_code, status.HTTP_200_OK)
        self.assertIn(self.tag, self.subscription.tags.all())

        unassign_response = self.client.patch(
            f'/subscription/tags/{self.tag.tag_id}/unassign/{self.subscription.subscription_id}'
        )
        self.assertEqual(unassign_response.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.tag, self.subscription.tags.all())

    def test_assign_and_unassign_are_idempotent(self):
        self.subscription.tags.add(self.tag)
        assign_response = self.client.patch(
            f'/subscription/tags/{self.tag.tag_id}/assign/{self.subscription.subscription_id}'
        )
        self.assertEqual(assign_response.status_code, status.HTTP_200_OK)

        self.subscription.tags.clear()
        unassign_response = self.client.patch(
            f'/subscription/tags/{self.tag.tag_id}/unassign/{self.subscription.subscription_id}'
        )
        self.assertEqual(unassign_response.status_code, status.HTTP_200_OK)

    def test_assign_rejects_missing_tag_or_subscription(self):
        cases = [
            (uuid.uuid4(), self.subscription.subscription_id),
            (self.tag.tag_id, uuid.uuid4()),
        ]
        for tag_id, subscription_id in cases:
            with self.subTest(tag_id=tag_id, subscription_id=subscription_id):
                response = self.client.patch(
                    f'/subscription/tags/{tag_id}/assign/{subscription_id}'
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_assign_rejects_foreign_tag(self):
        foreign = create_test_tag(sport_association=create_test_sport_association())

        response = self.client.patch(
            f'/subscription/tags/{foreign.tag_id}/assign/{self.subscription.subscription_id}'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_assignment_requires_authentication(self):
        self.client.force_authenticate(user=None)

        assign_response = self.client.patch(
            f'/subscription/tags/{self.tag.tag_id}/assign/{self.subscription.subscription_id}'
        )
        unassign_response = self.client.patch(
            f'/subscription/tags/{self.tag.tag_id}/unassign/{self.subscription.subscription_id}'
        )

        self.assertEqual(assign_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(unassign_response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionAddTests(SubscriptionViewsTestCase):
    def test_add_adult_subscription(self):
        payload = self.subscription_payload()

        response = self.client.post('/subscription/add', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription = Subscription.objects.filter(
            sport_association=self.sport_association,
            associate__tax_code=payload['associate_data']['tax_code'],
        ).first()
        self.assertIsNotNone(subscription)
        self.assertEqual(subscription.associate.first_name, 'Giovanni')

    def test_add_minor_with_tutor(self):
        payload = self.subscription_payload(
            is_minor=True,
            first_name='Mario',
            born_date='10/02/2015',
        )
        payload['associate_tutor_data'] = {
            'first_name': 'Luigi',
            'last_name': 'Barbieri',
            'sex': 'M',
            'tax_code': f'BRBLGU70B10{uuid.uuid4().hex[:4].upper()}T',
            'born_date': '10/02/1970',
            'born_city': 'Roma',
            'address': 'Via Roma 1',
            'address_city': 'Roma',
            'address_cap': '00100',
            'phone': '+39123456789',
            'email': 'luigi@example.com',
        }

        response = self.client.post('/subscription/add', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription = Subscription.objects.filter(
            sport_association=self.sport_association,
            associate__tax_code=payload['associate_data']['tax_code'],
        ).first()
        self.assertIsNotNone(subscription)
        self.assertIsNotNone(subscription.associate.get_main_tutor())

    def test_add_missing_required_sections_returns_bad_request(self):
        response = self.client.post(
            '/subscription/add',
            {'associate_data': {'first_name': 'Incomplete'}},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SubscriptionInfoTests(SubscriptionViewsTestCase):
    @patch('application.views.subscriptions_views.print_document_subscription.delay')
    def test_get_subscription_info(self, mock_print):
        subscription = self.create_subscription(with_payment=True, with_medical=True)

        response = self.client.get(f'/subscription/{subscription.subscription_id}/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['data']['info']['subscription_id'],
            str(subscription.subscription_id),
        )
        self.assertIn('courses', response.data['data'])

    def test_missing_subscription_returns_not_found(self):
        response = self.client.get(f'/subscription/{uuid.uuid4()}/info')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_foreign_subscription_is_forbidden(self):
        foreign = create_test_subscription(sport_association=create_test_sport_association())

        response = self.client.get(f'/subscription/{foreign.subscription_id}/info')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_info_requires_authentication(self):
        subscription = self.create_subscription()
        self.client.force_authenticate(user=None)

        response = self.client.get(f'/subscription/{subscription.subscription_id}/info')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionUpdateTests(SubscriptionViewsTestCase):
    @patch('application.views.subscriptions_views.print_document_subscription.delay')
    def test_update_subscription(self, mock_print):
        subscription = self.create_subscription(status_flag=Subscription.PENDING)

        response = self.client.patch(
            f'/subscription/{subscription.subscription_id}/update',
            {'status_flag': Subscription.ACCEPTED, 'notes': 'Updated'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status_flag, Subscription.ACCEPTED)
        self.assertEqual(subscription.notes, 'Updated')

    def test_update_missing_subscription_is_forbidden(self):
        response = self.client.patch(
            f'/subscription/{uuid.uuid4()}/update',
            {'notes': 'Updated'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_foreign_subscription_is_forbidden(self):
        foreign = create_test_subscription(sport_association=create_test_sport_association())

        response = self.client.patch(
            f'/subscription/{foreign.subscription_id}/update',
            {'notes': 'Denied'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_requires_authentication(self):
        subscription = self.create_subscription()
        self.client.force_authenticate(user=None)

        response = self.client.patch(
            f'/subscription/{subscription.subscription_id}/update',
            {'notes': 'Denied'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionStatusTests(SubscriptionViewsTestCase):
    def test_approve_pending_subscription(self):
        subscription = self.create_subscription(status_flag=Subscription.PENDING)

        response = self.client.post(f'/subscription/{subscription.subscription_id}/approve')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status_flag, Subscription.ACCEPTED)

    def test_approve_accepted_subscription_is_forbidden(self):
        subscription = self.create_subscription(status_flag=Subscription.ACCEPTED)

        response = self.client.post(f'/subscription/{subscription.subscription_id}/approve')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_approve_missing_subscription_returns_not_found(self):
        response = self.client.post(f'/subscription/{uuid.uuid4()}/approve')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_approve_foreign_subscription_is_forbidden(self):
        foreign = create_test_subscription(
            sport_association=create_test_sport_association(),
            status_flag=Subscription.PENDING,
        )

        response = self.client.post(f'/subscription/{foreign.subscription_id}/approve')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reject_pending_subscription(self):
        subscription = self.create_subscription(status_flag=Subscription.PENDING)

        response = self.client.post(f'/subscription/{subscription.subscription_id}/reject')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status_flag, Subscription.REJECTED)

    def test_reject_rejected_subscription_is_forbidden(self):
        subscription = self.create_subscription(status_flag=Subscription.REJECTED)

        response = self.client.post(f'/subscription/{subscription.subscription_id}/reject')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reject_missing_subscription_returns_not_found(self):
        response = self.client.post(f'/subscription/{uuid.uuid4()}/reject')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_status_mutations_require_authentication(self):
        subscription = self.create_subscription(status_flag=Subscription.PENDING)
        self.client.force_authenticate(user=None)

        approve_response = self.client.post(f'/subscription/{subscription.subscription_id}/approve')
        reject_response = self.client.post(f'/subscription/{subscription.subscription_id}/reject')

        self.assertEqual(approve_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(reject_response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionArchiveTests(SubscriptionViewsTestCase):
    def test_archive_toggles_subscription(self):
        subscription = self.create_subscription(archived=False)

        first_response = self.client.post(f'/subscription/{subscription.subscription_id}/archive')
        subscription.refresh_from_db()
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertTrue(subscription.archived)

        second_response = self.client.post(f'/subscription/{subscription.subscription_id}/archive')
        subscription.refresh_from_db()
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertFalse(subscription.archived)

    def test_archive_missing_subscription_returns_not_found(self):
        response = self.client.post(f'/subscription/{uuid.uuid4()}/archive')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_archive_foreign_subscription_is_forbidden(self):
        foreign = create_test_subscription(sport_association=create_test_sport_association())

        response = self.client.post(f'/subscription/{foreign.subscription_id}/archive')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_archive_requires_authentication(self):
        subscription = self.create_subscription()
        self.client.force_authenticate(user=None)

        response = self.client.post(f'/subscription/{subscription.subscription_id}/archive')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionDeleteTests(SubscriptionViewsTestCase):
    def test_delete_subscription(self):
        subscription = self.create_subscription()

        response = self.client.post(f'/subscription/{subscription.subscription_id}/delete')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Subscription.objects.filter(
            subscription_id=subscription.subscription_id
        ).exists())

    def test_delete_subscription_with_unpaid_payment(self):
        payment = create_test_payment(
            sport_association=self.sport_association,
            associate=self.associate,
            user=self.user,
            paid=False,
        )
        subscription = self.create_subscription(
            associate=self.associate,
            payment=payment,
        )

        response = self.client.post(f'/subscription/{subscription.subscription_id}/delete')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Subscription.objects.filter(
            subscription_id=subscription.subscription_id
        ).exists())

    def test_delete_missing_subscription_returns_not_found(self):
        response = self.client.post(f'/subscription/{uuid.uuid4()}/delete')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_foreign_subscription_is_forbidden(self):
        foreign = create_test_subscription(sport_association=create_test_sport_association())

        response = self.client.post(f'/subscription/{foreign.subscription_id}/delete')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_requires_authentication(self):
        subscription = self.create_subscription()
        self.client.force_authenticate(user=None)

        response = self.client.post(f'/subscription/{subscription.subscription_id}/delete')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionListTests(SubscriptionViewsTestCase):
    def test_list_returns_active_and_excludes_archived(self):
        active = self.create_subscription()
        archived = create_test_archived_subscription(sport_association=self.sport_association)

        response = self.client.get('/subscription/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription_ids = [item['subscription_id'] for item in response.data['data'].values()]
        self.assertIn(str(active.subscription_id), subscription_ids)
        self.assertNotIn(str(archived.subscription_id), subscription_ids)

    def test_list_empty(self):
        response = self.client.get('/subscription/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data'], {})

    def test_list_pagination(self):
        create_bulk_subscriptions(count=12, sport_association=self.sport_association)

        response = self.client.get('/subscription/list?pagination[perpage]=5&pagination[page]=1')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']), 5)
        self.assertEqual(response.data['meta']['total'], 12)

    def test_list_search(self):
        associate = create_test_associate(
            sport_association=self.sport_association,
            first_name='Searchable',
        )
        subscription = self.create_subscription(associate=associate)

        response = self.client.get('/subscription/list?query[generalSearch]=Searchable')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription_ids = [item['subscription_id'] for item in response.data['data'].values()]
        self.assertEqual(subscription_ids, [str(subscription.subscription_id)])

    def test_list_only_own_association(self):
        own = self.create_subscription()
        foreign = create_test_subscription(sport_association=create_test_sport_association())

        response = self.client.get('/subscription/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription_ids = [item['subscription_id'] for item in response.data['data'].values()]
        self.assertIn(str(own.subscription_id), subscription_ids)
        self.assertNotIn(str(foreign.subscription_id), subscription_ids)

    def test_list_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get('/subscription/list')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionListAllTests(SubscriptionViewsTestCase):
    def test_list_all_returns_non_archived_subscriptions(self):
        active = self.create_subscription()
        archived = create_test_archived_subscription(sport_association=self.sport_association)

        response = self.client.get('/subscription/list/all')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription_ids = [str(item['subscription_id']) for item in response.data['data'].values()]
        self.assertIn(str(active.subscription_id), subscription_ids)
        self.assertNotIn(str(archived.subscription_id), subscription_ids)

    def test_list_all_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get('/subscription/list/all')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionListArchivedTests(SubscriptionViewsTestCase):
    def test_list_archived_only(self):
        active = self.create_subscription()
        archived = create_test_archived_subscription(sport_association=self.sport_association)

        response = self.client.get('/subscription/list/archived')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subscription_ids = [item['subscription_id'] for item in response.data['data'].values()]
        self.assertNotIn(str(active.subscription_id), subscription_ids)
        self.assertIn(str(archived.subscription_id), subscription_ids)

    def test_list_archived_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get('/subscription/list/archived')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
