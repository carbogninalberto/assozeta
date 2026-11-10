"""Which certificate a registration links, and which subscriptions a member account renews."""
import copy
import datetime
import uuid

from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from application.models.subscriptions_models import MedicalCertificate, Subscription
from application.models.user_models import User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_associate, create_test_family, create_test_medical_certificate, create_test_sport_association,
    create_test_subscription, create_test_user,
)

EXPIRATION = '31/12/2031'


def new_tax_code():
    return f'BRBGVN90B10{uuid.uuid4().hex[:4].upper()}T'


def payload(*, tax_code=None, medical_id=None, **extra):
    body = {
        'new_user_account': {'new_member': False},
        'plan_id': None, 'membership_plan_id': None,
        'associate_data': {'type': 1, 'is_minor': False, 'first_name': 'Giovanni', 'last_name': 'Barbieri', 'sex': 'M',
                           'tax_code': tax_code or new_tax_code(), 'born_date': '10/02/1990', 'born_city': 'Roma',
                           'address': 'Via Roma 1', 'address_city': 'Roma', 'address_cap': '00100', 'phone': None, 'email': None},
        'associate_tutor_data': None,
        'signature': {'there_is_signature': False, 'data': None},
        'medical_certificate': {'medical_id': medical_id, 'filename': None},
        'custom_data': {}, 'additional_fields': {},
        **extra,
    }
    if medical_id is not None:
        body['medical_certificate']['certificate_expring_date'] = EXPIRATION
    return copy.deepcopy(body)


def as_user(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(RefreshToken.for_user(user).access_token))
    return client


class Base(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.other_owner = create_test_user(role=User.ASSOCIATION)
        self.other_association = create_test_sport_association(user=self.other_owner)
        self.member = create_test_user(role=User.ATHLETE)

    def created(self, tax_code, association=None):
        return Subscription.objects.get(associate__tax_code=tax_code, sport_association=association or self.sport_association,
                                        archived=False, start_date__gte=timezone.now().date() - datetime.timedelta(days=366))

    def past_subscription(self, association, user, **associate):
        person = create_test_associate(sport_association=association, user=user, **associate)
        start = timezone.now().date() - datetime.timedelta(days=800)
        return create_test_subscription(sport_association=association, associate=person, user=user, start_date=start,
                                        end_date=start + datetime.timedelta(days=364))


class CertificateLinkTests(Base):
    def add(self, client, certificate, **extra):
        before = (certificate.user_id, certificate.expiration_date)
        body = payload(medical_id=str(certificate.medical_id), **extra)
        response = client.post('/subscription/add', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        certificate.refresh_from_db()
        association = self.other_association if extra.get('sport_association') == self.other_owner.username else None
        return self.created(body['associate_data']['tax_code'], association), before

    def assert_linked(self, subscription, certificate):
        self.assertEqual(subscription.medical_id, certificate.medical_id)
        self.assertEqual(certificate.expiration_date, datetime.date(2031, 12, 31))

    def assert_not_linked(self, subscription, certificate, before):
        self.assertIsNone(subscription.medical_id)
        self.assertEqual((certificate.user_id, certificate.expiration_date), before)

    def test_association_links_its_own_certificate(self):
        certificate = create_test_medical_certificate(user=self.user)
        subscription, _ = self.add(self.client, certificate)
        self.assert_linked(subscription, certificate)

    def test_association_links_an_upload_without_account(self):
        certificate = MedicalCertificate.objects.create(user=None)
        subscription, _ = self.add(self.client, certificate)
        self.assert_linked(subscription, certificate)
        self.assertEqual(certificate.user_id, self.user.pk)

    def test_association_links_the_certificate_of_one_of_its_subscriptions(self):
        certificate = create_test_medical_certificate(user=self.member)
        previous = self.past_subscription(self.sport_association, self.member)
        previous.medical = certificate
        previous.save()
        subscription, _ = self.add(self.client, certificate)
        self.assert_linked(subscription, certificate)

    def test_certificate_of_another_account_is_not_linked(self):
        certificate = create_test_medical_certificate(user=self.other_owner)
        subscription, before = self.add(self.client, certificate)
        self.assert_not_linked(subscription, certificate, before)

    def test_certificate_of_another_associations_subscription_is_not_linked(self):
        certificate = MedicalCertificate.objects.create(user=None)
        foreign = self.past_subscription(self.other_association, self.other_owner)
        foreign.medical = certificate
        foreign.save()
        subscription, before = self.add(self.client, certificate)
        self.assert_not_linked(subscription, certificate, before)

    def test_public_form_links_its_upload(self):
        certificate = MedicalCertificate.objects.create(user=None)
        subscription, _ = self.add(APIClient(), certificate, sport_association=self.user.username)
        self.assert_linked(subscription, certificate)
        self.assertEqual(certificate.user_id, self.user.pk)

    def test_public_form_does_not_link_the_certificate_of_another_account(self):
        for owner in (self.other_owner, self.member):
            with self.subTest(owner=owner.role):
                certificate = create_test_medical_certificate(user=owner)
                subscription, before = self.add(APIClient(), certificate, sport_association=self.user.username)
                self.assert_not_linked(subscription, certificate, before)

    def test_member_links_its_own_certificate(self):
        certificate = create_test_medical_certificate(user=self.member)
        subscription, _ = self.add(as_user(self.member), certificate, sport_association=self.user.username)
        self.assert_linked(subscription, certificate)

    def test_member_does_not_link_the_certificate_of_another_account(self):
        certificate = create_test_medical_certificate(user=self.other_owner)
        subscription, before = self.add(as_user(self.member), certificate, sport_association=self.user.username)
        self.assert_not_linked(subscription, certificate, before)


class MemberRenewalTests(Base):
    def renew(self, account, association, associate, **extra):
        body = payload(tax_code=associate.tax_code, sport_association={'sport_association_id': str(association.pk)}, **extra)
        body['associate_data'].update(first_name=associate.first_name, last_name=associate.last_name,
                                      associate_id=str(associate.associate_id))
        return as_user(account).post('/subscription/renew', body, format='json'), body

    def test_member_renews_its_own_subscription(self):
        previous = self.past_subscription(self.sport_association, self.member)
        response, _ = self.renew(self.member, self.sport_association, previous.associate)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.created(previous.associate.tax_code).associate_id, previous.associate_id)

    def test_member_renews_by_tax_code(self):
        previous = self.past_subscription(self.sport_association, self.member)
        body = payload(tax_code=previous.associate.tax_code.lower(),
                       sport_association={'sport_association_id': str(self.sport_association.pk)})
        response = as_user(self.member).post('/subscription/renew', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_member_renews_a_family_member(self):
        family = create_test_family()
        self.past_subscription(self.sport_association, self.member, family=family)
        relative = self.past_subscription(self.sport_association, self.user, family=family)
        response, _ = self.renew(self.member, self.sport_association, relative.associate)
        self.assertEqual(response.status_code, 200, response.data)

    def test_member_renews_a_person_with_the_tax_code_of_its_subscription(self):
        own = self.past_subscription(self.other_association, self.member)
        same = self.past_subscription(self.sport_association, self.user, tax_code=own.associate.tax_code)
        response, _ = self.renew(self.member, self.sport_association, same.associate)
        self.assertEqual(response.status_code, 200, response.data)

    def test_member_does_not_renew_another_member(self):
        self.past_subscription(self.sport_association, self.member)
        stranger = self.past_subscription(self.sport_association, self.user)
        before = Subscription.objects.count()
        for by_id in (True, False):
            with self.subTest(by_id=by_id):
                response, body = self.renew(self.member, self.sport_association, stranger.associate)
                if not by_id:
                    del body['associate_data']['associate_id']
                    response = as_user(self.member).post('/subscription/renew', body, format='json')
                self.assertEqual(response.status_code, 403, response.data)
                self.assertEqual(response.json(), {'detail': 'User not allowed.'})
                self.assertEqual(Subscription.objects.count(), before)

    def test_member_does_not_renew_in_another_association(self):
        previous = self.past_subscription(self.sport_association, self.member)
        before = Subscription.objects.count()
        response, _ = self.renew(self.member, self.other_association, previous.associate)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(Subscription.objects.count(), before)
        self.assertFalse(Subscription.objects.filter(sport_association=self.other_association).exists())

    def test_member_renewal_keeps_the_account(self):
        other = create_test_user(role=User.ATHLETE)
        previous = self.past_subscription(self.sport_association, self.member)
        response, _ = self.renew(self.member, self.sport_association, previous.associate, user={'email': other.email})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotEqual(self.created(previous.associate.tax_code).user_id, other.pk)

    def test_association_renewal_sets_the_account(self):
        previous = self.past_subscription(self.sport_association, self.user)
        body = payload(tax_code=previous.associate.tax_code, user={'email': self.member.email})
        body['associate_data'].update(first_name=previous.associate.first_name, last_name=previous.associate.last_name)
        response = self.client.post('/subscription/renew', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.created(previous.associate.tax_code).user_id, self.member.pk)
