"""Subscriptions created by an association only use that association's own members, tags and courses."""
import copy
import uuid

from application.models.subscriptions_models import Payment, Subscription
from application.models.user_models import User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_associate, create_test_course, create_test_sport_association, create_test_tag, create_test_user,
)


def payload(*, sport_association=None, **extra):
    tax_code = f'BRBGVN90B10{uuid.uuid4().hex[:4].upper()}T'
    body = {
        'new_user_account': {'new_member': False},
        'plan_id': None, 'membership_plan_id': None,
        'associate_data': {'type': 1, 'is_minor': False, 'first_name': 'Giovanni', 'last_name': 'Barbieri', 'sex': 'M',
                           'tax_code': tax_code, 'born_date': '10/02/1990', 'born_city': 'Roma', 'address': 'Via Roma 1',
                           'address_city': 'Roma', 'address_cap': '00100', 'phone': None, 'email': None},
        'associate_tutor_data': None,
        'signature': {'there_is_signature': False, 'data': None},
        'medical_certificate': {'medical_id': None, 'filename': None, 'certificate_expring_date': None},
        'custom_data': {}, 'additional_fields': {},
        **extra,
    }
    if sport_association is not None:
        body['sport_association'] = sport_association
    return copy.deepcopy(body), tax_code


class SubscriptionCreateScopeTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.other_owner = create_test_user(role=User.ASSOCIATION)
        self.other_association = create_test_sport_association(user=self.other_owner)

    def counts(self):
        return Subscription.objects.count(), Payment.objects.count()

    def test_quick_resubscription_uses_own_members(self):
        member = create_test_associate(sport_association=self.sport_association, user=self.user)
        response = self.client.post('/subscription/add?m=only_associate', {'associate': str(member.associate_id)}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Subscription.objects.filter(associate=member, sport_association=self.sport_association).exists())

    def test_quick_resubscription_refuses_another_associations_member(self):
        member = create_test_associate(sport_association=self.other_association, user=self.other_owner)
        before = self.counts()
        response = self.client.post('/subscription/add?m=only_associate', {'associate': str(member.associate_id)}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.json(), {'detail': 'User not allowed.'})
        self.assertEqual(self.counts(), before)

    def test_named_association_is_the_own_one(self):
        body, tax_code = payload(sport_association=self.user.username)
        response = self.client.post('/subscription/add', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Subscription.objects.filter(associate__tax_code=tax_code, sport_association=self.sport_association).exists())

    def test_named_association_of_another_owner_is_refused(self):
        body, tax_code = payload(sport_association=self.other_owner.username)
        before = self.counts()
        response = self.client.post('/subscription/add', body, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.json(), {'detail': 'User not allowed.'})
        self.assertEqual(self.counts(), before)
        self.assertFalse(Subscription.objects.filter(associate__tax_code=tax_code).exists())

    def test_renewal_tags_are_the_associations_own(self):
        own_tag = create_test_tag(sport_association=self.sport_association)
        other_tag = create_test_tag(sport_association=self.other_association)
        body, tax_code = payload(tags=[str(own_tag.tag_id), str(other_tag.tag_id)])
        response = self.client.post('/subscription/renew', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        subscription = Subscription.objects.get(associate__tax_code=tax_code)
        self.assertEqual(list(subscription.tags.all()), [own_tag])

    def test_renewal_courses_are_the_associations_own(self):
        other_course = create_test_course(sport_association=self.other_association)
        body, tax_code = payload(courses=[{'value': str(other_course.course_id)}])
        before = self.counts()
        response = self.client.post('/subscription/renew', body, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.counts(), before)
        self.assertFalse(other_course.coursesubscription_set.exists())
