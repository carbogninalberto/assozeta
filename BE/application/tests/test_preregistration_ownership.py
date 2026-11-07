"""Who owns a subscription created from a (pre)registration link."""
import copy
import uuid

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from application.models.subscriptions_models import Subscription
from application.models.user_models import User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_associate, create_test_sport_association, create_test_subscription, create_test_user,
)


def payload(association_username, *, preregistration, new_member=None, tax_code=None):
    body = {
        'new_user_account': {'new_member': False, 'new_member_info': {
            'first_name': None, 'last_name': None, 'phone': None, 'email': None}},
        'sport_association': association_username,
        'plan_id': None, 'membership_plan_id': None,
        'associate_data': {'type': 1, 'is_minor': False, 'first_name': 'Giovanni', 'last_name': 'Barbieri', 'sex': 'M',
                           'tax_code': tax_code or f'BRBGVN90B10{uuid.uuid4().hex[:4].upper()}T', 'born_date': '10/02/1990',
                           'born_city': 'Roma', 'address': 'Via Roma 1', 'address_city': 'Roma', 'address_cap': '00100',
                           'phone': None, 'email': None},
        'associate_tutor_data': None,
        'signature': {'there_is_signature': False, 'data': None},
        'medical_certificate': {'medical_id': None, 'filename': None, 'certificate_expring_date': None},
        'custom_data': {}, 'additional_fields': {},
        'subscription_id': None,
    }
    if preregistration:
        body['preregistration'] = True
    if new_member:
        body['new_user_account'] = {'new_member': True, 'new_member_info': new_member}
    return copy.deepcopy(body)


class PreregistrationOwnershipTests(BaseAPITestCase):
    def anonymous(self):
        return APIClient()

    def as_user(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(RefreshToken.for_user(user).access_token))
        return client

    def created(self, tax_code):
        return Subscription.objects.get(associate__tax_code=tax_code, sport_association=self.sport_association)

    def test_account_created_in_the_wizard_owns_its_preregistration(self):
        for preregistration in (False, True):
            with self.subTest(preregistration=preregistration):
                email = f'new-{uuid.uuid4().hex[:8]}@example.test'
                tax_code = f'BRBGVN90B10{uuid.uuid4().hex[:4].upper()}T'
                body = payload(self.user.username, preregistration=preregistration, tax_code=tax_code, new_member={
                    'first_name': 'Giovanni', 'last_name': 'Barbieri', 'phone': '3330000000', 'email': email})
                response = self.anonymous().post('/subscription/add', body, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                account = User.objects.get(email=email)
                subscription = self.created(tax_code)
                # Previously a preregistration belonged to the association account.
                self.assertEqual(subscription.user, account)
                self.assertEqual(subscription.associate.user, account)
                self.assertEqual(subscription.payment.user, account)

    def test_preregistration_without_account_matches_a_normal_registration(self):
        owners = {}
        for preregistration in (False, True):
            tax_code = f'BRBGVN90B10{uuid.uuid4().hex[:4].upper()}T'
            response = self.anonymous().post('/subscription/add', payload(
                self.user.username, preregistration=preregistration, tax_code=tax_code), format='json')
            self.assertEqual(response.status_code, 200, response.data)
            owners[preregistration] = self.created(tax_code).user
        self.assertEqual(owners[True], owners[False])

    def test_logged_in_member_owns_its_preregistration(self):
        member = create_test_user(role=User.ATHLETE)
        tax_code = f'BRBGVN90B10{uuid.uuid4().hex[:4].upper()}T'
        response = self.as_user(member).post('/subscription/add', payload(
            self.user.username, preregistration=True, tax_code=tax_code), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.created(tax_code).user, member)

    def renewable(self, owner, association=None):
        association = association or self.sport_association
        associate = create_test_associate(sport_association=association, user=owner)
        return create_test_subscription(sport_association=association, associate=associate, user=owner,
                                        status_flag=Subscription.ACCEPTED)

    def renew(self, client, renewed):
        body = payload(self.user.username, preregistration=True)
        body['subscription_id'] = str(renewed.subscription_id)
        return client.post('/subscription/add', body, format='json')

    def renewals_of(self, renewed):
        return Subscription.objects.filter(associate__tax_code=renewed.associate.tax_code).exclude(pk=renewed.pk)

    def test_renewal_keeps_the_owner_of_the_renewed_subscription(self):
        member = create_test_user(role=User.ATHLETE)
        renewed = self.renewable(member)
        response = self.renew(self.as_user(member), renewed)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.renewals_of(renewed).get().user, member)

    def test_association_renews_its_member_and_the_member_keeps_it(self):
        member = create_test_user(role=User.ATHLETE)
        renewed = self.renewable(member)
        response = self.renew(self.as_user(self.user), renewed)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.renewals_of(renewed).get().user, member)

    def test_renewal_requires_the_owner_or_the_association(self):
        owner = create_test_user(role=User.ATHLETE)
        renewed = self.renewable(owner)
        for label, client in (('another member', self.as_user(create_test_user(role=User.ATHLETE))),
                              ('anonymous', self.anonymous())):
            with self.subTest(label):
                response = self.renew(client, renewed)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(self.renewals_of(renewed).exists())

    def test_renewal_stays_within_the_association_of_the_link(self):
        member = create_test_user(role=User.ATHLETE)
        other_association = create_test_sport_association()
        renewed = self.renewable(member, other_association)
        response = self.renew(self.as_user(member), renewed)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(self.renewals_of(renewed).exists())
        response = self.renew(self.as_user(self.user), renewed)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(self.renewals_of(renewed).exists())
