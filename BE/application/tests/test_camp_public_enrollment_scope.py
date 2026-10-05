"""Real handler/ORM regression cases for public camp signup scope.

Authentication uses the standard test client; no handler, payment or tenant
checks are mocked. These are prepared tests, not browser capture evidence.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone

from application.models import (CampsAndRetreats, CampsAndRetreatsPeriod,
    CampsAndRetreatsPeriodsService, CampsAndRetreatsSubscription,
    CampsAndRetreatsSubscriptionPeriod, Payment, User)
from application.models.user_models import Family
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (create_test_associate,
    create_test_payment_category, create_test_sport_association,
    create_test_subscription, create_test_user)


class PublicCampEnrollmentScopeTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.athlete = create_test_user(role=User.ATHLETE)
        self.person = create_test_associate(sport_association=self.sport_association, user=self.athlete)
        self.registration = create_test_subscription(sport_association=self.sport_association,
            associate=self.person, user=self.athlete)
        self.unrelated = create_test_subscription(sport_association=self.sport_association)
        self.camp = CampsAndRetreats.objects.create(sport_association=self.sport_association, title='Owned camp')
        self.period = self.make_period(self.camp)
        self.category = create_test_payment_category(sport_association=self.sport_association)
        self.service = CampsAndRetreatsPeriodsService.objects.create(camps_and_retreats_period=self.period,
            title='Pranzo', fee=Decimal('15.00'), payment_category=self.category)
        self.foreign_association = create_test_sport_association()
        self.foreign_registration = create_test_subscription(sport_association=self.foreign_association)
        self.foreign_camp = CampsAndRetreats.objects.create(sport_association=self.foreign_association, title='Foreign camp')
        self.foreign_period = self.make_period(self.foreign_camp)
        self.foreign_service = CampsAndRetreatsPeriodsService.objects.create(camps_and_retreats_period=self.foreign_period,
            title='Foreign service', fee=Decimal('10.00'), payment_category=self.category)
        self.athlete_client = self.create_authenticated_client(self.athlete)

    def make_period(self, camp):
        return CampsAndRetreatsPeriod.objects.create(camps_and_retreats=camp, title='Allenamento',
            start_date=timezone.now(), end_date=timezone.now()+timedelta(days=1), fee=Decimal('90.00'))

    def payload(self, registration=None, period=None, service=None):
        return {'subscription':str((registration or self.registration).pk), 'periods':[
            {'camps_and_retreats_period':str((period or self.period).pk), 'services':[
                {'camps_and_retreats_period_service_id':str((service or self.service).pk)}]}]}

    def snapshot(self):
        return {model.__name__:list(model._base_manager.order_by('pk').values()) for model in
            (CampsAndRetreatsSubscription, CampsAndRetreatsSubscriptionPeriod, Payment)}

    def assert_denied_unchanged(self, client, camp, payload, code=403):
        before=self.snapshot()
        response=client.post(f'/camps-and-retreats/{camp.pk}/subscriptions/add', payload, format='json')
        self.assertEqual(response.status_code, code, response.content)
        self.assertEqual(self.snapshot(), before)

    def test_actual_owned_athlete_and_association_success_persist105_unpaid(self):
        for client, registration in [(self.athlete_client,self.registration),(self.client,self.unrelated)]:
            with self.subTest(identity=registration.user_id):
                response=client.post(f'/camps-and-retreats/{self.camp.pk}/subscriptions/add',self.payload(registration),format='json')
                self.assertEqual(response.status_code,201,response.content)
                enrollment=CampsAndRetreatsSubscription.objects.get(pk=response.data['subscription']['camps_and_retreats_subscription_id'])
                joined=enrollment.campsandretreatssubscriptionperiod_set.get()
                self.assertEqual(enrollment.subscription_id,registration.pk)
                self.assertEqual(joined.camps_and_retreats_period_id,self.period.pk)
                self.assertEqual(list(joined.camps_and_retreats_period_services.values_list('pk',flat=True)),[self.service.pk])
                self.assertEqual(joined.payment.amount,Decimal('105.00'))
                self.assertFalse(joined.payment.paid)
                self.assertEqual(joined.payment.sport_association_id,self.sport_association.pk)

    def test_foreign_camp_person_period_service_and_other_period_service_rejected_without_writes(self):
        same_camp_other_period=self.make_period(self.camp)
        same_camp_other_service=CampsAndRetreatsPeriodsService.objects.create(camps_and_retreats_period=same_camp_other_period,
            title='Other period service',fee=5,payment_category=self.category)
        attempts=[(self.client,self.foreign_camp,self.payload(self.foreign_registration,self.foreign_period,self.foreign_service)),
            (self.client,self.camp,self.payload(self.foreign_registration)),
            (self.athlete_client,self.foreign_camp,self.payload()),
            (self.athlete_client,self.camp,self.payload(self.unrelated)),
            (self.client,self.camp,self.payload(period=self.foreign_period)),
            (self.athlete_client,self.camp,self.payload(service=self.foreign_service)),
            (self.athlete_client,self.camp,self.payload(service=same_camp_other_service))]
        for client,camp,payload in attempts:
            with self.subTest(camp=camp.pk,payload=payload):self.assert_denied_unchanged(client,camp,payload)

    def test_actual_nonblank_owned_family_allows_member_but_null_family_never_matches(self):
        self.assertIsNone(self.person.family_id)
        self.assertIsNone(self.unrelated.associate.family_id)
        self.assert_denied_unchanged(self.athlete_client,self.camp,self.payload(self.unrelated))
        family=Family.objects.create();self.person.family=family;self.person.save(update_fields=['family'])
        other=self.unrelated.associate;other.family=family;other.save(update_fields=['family'])
        response=self.athlete_client.post(f'/camps-and-retreats/{self.camp.pk}/subscriptions/add',self.payload(self.unrelated),format='json')
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(CampsAndRetreatsSubscription.objects.get(pk=response.data['subscription']['camps_and_retreats_subscription_id']).subscription_id,self.unrelated.pk)

    def test_reader_permission_and_malformed_or_duplicate_selections_leave_state_unchanged(self):
        reader=create_test_user(role=User.COLLABORATOR,connected_user=self.user,collaborator_role=3,
            collaborator_permissions=['association.campsandretreats.read'])
        self.assert_denied_unchanged(self.create_authenticated_client(reader),self.camp,self.payload())
        empty_person=self.payload();empty_person['subscription']=''
        malformed=self.payload();malformed['periods']=[{'camps_and_retreats_period':'not-an-id','services':[]}]
        duplicate=self.payload();duplicate['periods'].append(deepcopy(duplicate['periods'][0]))
        duplicate_service=self.payload();duplicate_service['periods'][0]['services'].append(deepcopy(duplicate_service['periods'][0]['services'][0]))
        for payload in (empty_person,malformed,duplicate,duplicate_service):
            with self.subTest(payload=payload):self.assert_denied_unchanged(self.athlete_client,self.camp,payload,400)

    def test_omitted_optional_services_creates_only90_unpaid(self):
        payload=self.payload();payload['periods'][0].pop('services')
        response=self.athlete_client.post(f'/camps-and-retreats/{self.camp.pk}/subscriptions/add',payload,format='json')
        self.assertEqual(response.status_code,201,response.content)
        joined=CampsAndRetreatsSubscriptionPeriod.objects.get(camps_and_retreats_subscription_id=response.data['subscription']['camps_and_retreats_subscription_id'])
        self.assertFalse(joined.camps_and_retreats_period_services.exists())
        self.assertEqual(joined.payment.amount,Decimal('90.00'))
        self.assertFalse(joined.payment.paid)

    def test_actual_transaction_rolls_back_enrollment_if_payment_generation_fails(self):
        # Failure injection proves rollback only; it never supplies success evidence.
        before=self.snapshot()
        with patch('application.views.camp_and_retreats_views.generate_payment_for_periods',side_effect=RuntimeError('failure')):
            response=self.athlete_client.post(f'/camps-and-retreats/{self.camp.pk}/subscriptions/add',self.payload(),format='json')
        self.assertEqual(response.status_code,500,response.content)
        self.assertEqual(self.snapshot(),before)
