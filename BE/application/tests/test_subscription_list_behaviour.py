"""
Behaviour and query-count tests for the subscription list endpoints.

They pin the payload of ``/subscription/list`` (default, ``m=1``, ``field``
and athlete modes) and ``/subscription/list/archived`` so query and
serialization optimizations cannot change what the UI receives.
"""
import datetime
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.subscriptions_models import Subscription
from application.models.user_models import User
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_billing_subscription,
    create_test_course,
    create_test_course_subscription,
    create_test_document,
    create_test_medical_certificate,
    create_test_payment,
    create_test_sport_association,
    create_test_subscription,
    create_test_tag,
    create_test_user,
)

# django-silk (development only) records each request and EXPLAINs every query.
IGNORED_QUERY = re.compile(r'silk_|^(EXPLAIN|SAVEPOINT|RELEASE SAVEPOINT)')

TODAY = datetime.date.today()
PAST = {'start_date': TODAY - datetime.timedelta(days=730), 'end_date': TODAY - datetime.timedelta(days=366)}
CURRENT = {'start_date': TODAY - datetime.timedelta(days=30), 'end_date': TODAY + datetime.timedelta(days=330)}
FUTURE = {'start_date': TODAY + datetime.timedelta(days=10), 'end_date': TODAY + datetime.timedelta(days=375)}


class SubscriptionListScenarioMixin:
    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def associate(self, sport_association=None, **kwargs):
        return create_test_associate(sport_association=sport_association or self.sport_association, **kwargs)

    def subscription(self, associate=None, sport_association=None, **kwargs):
        sport_association = sport_association or self.sport_association
        if associate is None:
            associate = self.associate(sport_association=sport_association)
        return create_test_subscription(sport_association=sport_association, associate=associate, **kwargs)

    def move_dates(self, sub, **dates):
        # Bypasses the manager's overlap guard, which rejects these on create.
        Subscription.objects.filter(pk=sub.pk).update(**dates)

    def list_rows(self, query='', path='/subscription/list'):
        response = self.client.get(f'{path}?{query}' if query else path)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return response, {str(row['subscription_id']): row for row in response.data['data'].values()}

    def count_queries(self, path):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(path)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return len([q for q in ctx.captured_queries if not IGNORED_QUERY.search(q['sql'])])


class RenewalAvailableScenarioMixin(SubscriptionListScenarioMixin):
    """Past subscriptions whose renewal state depends on later subscriptions."""

    def build_renewal_scenario(self):
        s = {}
        s['past_only'] = self.subscription(**PAST)

        renewed = self.associate()
        s['renewed_past'] = self.subscription(associate=renewed, **PAST)
        s['renewed_current'] = self.subscription(associate=renewed, **CURRENT)

        archived_future = self.associate()
        s['past_with_archived_future'] = self.subscription(associate=archived_future, **PAST)
        self.subscription(associate=archived_future, archived=True, **FUTURE)

        boundary = self.associate()
        s['past_with_boundary_future'] = self.subscription(associate=boundary, **PAST)
        self.move_dates(self.subscription(associate=boundary, **FUTURE), start_date=PAST['end_date'])

        overlapping = self.associate()
        s['past_with_overlapping_later'] = self.subscription(associate=overlapping, **PAST)
        self.move_dates(self.subscription(associate=overlapping, **FUTURE),
                        start_date=PAST['end_date'] - datetime.timedelta(days=1))

        # Same person, different associate record (deleted, other case): matched case-insensitively.
        case_owner = self.associate(tax_code='rnwcas90a01h501x')
        s['past_with_case_insensitive_future'] = self.subscription(associate=case_owner, **PAST)
        self.subscription(associate=self.associate(tax_code='RNWCAS90A01H501X', deleted=True), **FUTURE)

        # Same tax code renewed in another association does not count.
        cross_owner = self.associate(tax_code='RNWCRS90A01H501X')
        s['past_with_foreign_future'] = self.subscription(associate=cross_owner, **PAST)
        other_association = create_test_sport_association()
        self.subscription(
            associate=self.associate(sport_association=other_association, tax_code='RNWCRS90A01H501X'),
            sport_association=other_association,
            **FUTURE,
        )

        # The associate of a renewal may since have been moved to another association
        # (AssociateSerializer.create reuses associate_id): it still counts.
        moved_owner = self.associate(tax_code='RNWMOV90A01H501X')
        s['past_with_future_of_moved_associate'] = self.subscription(associate=moved_owner, **PAST)
        self.subscription(
            associate=self.associate(sport_association=create_test_sport_association(), tax_code='RNWMOV90A01H501X'),
            **FUTURE,
        )

        # In the paginated list a missing tax code never matches.
        s['past_without_tax_code'] = self.subscription(associate=self.associate(tax_code=None), **PAST)
        self.subscription(associate=self.associate(tax_code=None), **FUTURE)
        return s

    expected_renewal = {
        'past_only': True,
        'renewed_past': False,
        'renewed_current': False,
        'past_with_archived_future': True,
        'past_with_boundary_future': False,
        'past_with_overlapping_later': True,
        'past_with_case_insensitive_future': False,
        'past_with_foreign_future': True,
        'past_with_future_of_moved_associate': False,
        'past_without_tax_code': True,
    }


class SubscriptionListRenewalTests(RenewalAvailableScenarioMixin, TestCase):
    def test_renewal_available_per_scenario(self):
        scenario = self.build_renewal_scenario()

        _, rows = self.list_rows('pagination[perpage]=100')

        actual = {name: rows[str(sub.subscription_id)]['renewal_available'] for name, sub in scenario.items()}
        self.assertEqual(actual, self.expected_renewal)

    def test_current_and_next_year_flags(self):
        current = self.subscription(**CURRENT)
        past = self.subscription(**PAST)
        future = self.subscription(**FUTURE)

        _, rows = self.list_rows()

        flags = {key: (rows[str(sub.subscription_id)]['current_year'], rows[str(sub.subscription_id)]['next_years'])
                 for key, sub in {'current': current, 'past': past, 'future': future}.items()}
        self.assertEqual(flags, {'current': (True, False), 'past': (False, False), 'future': (False, True)})


class SubscriptionListPayloadTests(SubscriptionListScenarioMixin, TestCase):
    def test_full_row_payload(self):
        associate = self.associate(first_name='Mario', last_name='Rossi', born_date=datetime.date(1990, 5, 17))
        document = create_test_document()
        medical_document = create_test_document()
        medical = create_test_medical_certificate(
            user=self.user, document=medical_document,
            # The label counts days from today's date in Rome, which runs ahead of UTC late in the evening.
            expiration_date=timezone.localtime(timezone.now(), ZoneInfo('Europe/Rome')).date() + datetime.timedelta(days=40),
        )
        payment = create_test_payment(associate=associate, sport_association=self.sport_association,
                                      amount=Decimal('30.50'), paid=True)
        create_test_payment(associate=associate, sport_association=self.sport_association,
                            amount=Decimal('20.25'), paid=False)
        create_test_payment(associate=associate, sport_association=self.sport_association,
                            amount=Decimal('999.00'), paid=False, archived=True)
        sub = self.subscription(associate=associate, medical=medical, document_pdf=document,
                                payment=payment, notes='note', **CURRENT)
        tag_a = create_test_tag(sport_association=self.sport_association, tag_name='Agonisti')
        tag_b = create_test_tag(sport_association=self.sport_association, tag_name='Under 18')
        sub.tags.add(tag_a, tag_b)

        _, rows = self.list_rows()
        row = rows[str(sub.subscription_id)]

        self.assertEqual(row['associate']['first_name'], 'Mario')
        self.assertEqual(row['associate']['associate_id'], str(associate.associate_id))
        self.assertEqual(row['user']['user_id'], str(sub.user.user_id))
        self.assertEqual(sorted(t['tag_name'] for t in row['tags']), ['Agonisti', 'Under 18'])
        self.assertEqual(row['payments_info'], {'total': 50.75, 'to_be_paid': 20.25})
        self.assertFalse(row['all_payments_paid'])
        self.assertEqual(row['payment'], {
            'payment_id': str(payment.payment_id), 'amount': 30.5, 'paid': True,
            'payment_date': payment.payment_date,
        })
        self.assertEqual(row['medical'], str(medical.medical_id))
        self.assertEqual(row['medical_document'], str(medical_document.document_id))
        self.assertEqual(row['medical_token'], medical_document.token)
        self.assertEqual(row['medical_expiration_date'], medical.expiration_date)
        self.assertEqual(row['plain_medical_label'], '40 giorni rimanenti')
        self.assertEqual(row['document'], str(document.document_id))
        self.assertEqual(row['document_token'], document.token)
        self.assertEqual(row['age'], associate.calculate_age())
        self.assertEqual(row['notes'], 'note')
        self.assertFalse(row['signature_present'])
        self.assertFalse(row['renewal_available'])

    def test_row_without_optional_relations(self):
        sub = self.subscription(**CURRENT)

        _, rows = self.list_rows()
        row = rows[str(sub.subscription_id)]

        self.assertEqual(row['tags'], [])
        self.assertIsNone(row['payment'])
        self.assertIsNone(row['medical'])
        self.assertIsNone(row['medical_document'])
        self.assertIsNone(row['document'])
        self.assertEqual(row['payments_info'], {'total': 0.0, 'to_be_paid': 0.0})
        self.assertTrue(row['all_payments_paid'])

    def test_meta_and_default_order(self):
        subs = [self.subscription(**CURRENT) for _ in range(7)]

        response, _ = self.list_rows('pagination[perpage]=3&pagination[page]=2')

        expected = [str(s.subscription_id) for s in reversed(subs)][3:6]
        self.assertEqual([row['subscription_id'] for row in response.data['data'].values()], expected)
        meta = response.data['meta']
        self.assertEqual((meta['total'], meta['page'], meta['pages'], meta['perpage']), (7, 2, 3, 3))
        self.assertEqual([str(i) for i in meta['rowIds']], expected)


class SubscriptionListFilterTests(SubscriptionListScenarioMixin, TestCase):
    def ids(self, query):
        response, rows = self.list_rows(query + '&pagination[perpage]=100')
        return set(rows), response.data['meta']['total']

    def test_tags_any_and_all(self):
        tag_a = create_test_tag(sport_association=self.sport_association)
        tag_b = create_test_tag(sport_association=self.sport_association)
        only_a = self.subscription()
        only_a.tags.add(tag_a)
        both = self.subscription()
        both.tags.add(tag_a, tag_b)
        self.subscription()

        any_ids, _ = self.ids(f'query[tags]={tag_a.tag_id},{tag_b.tag_id}')
        all_ids, all_total = self.ids(f'query[tags]={tag_a.tag_id},{tag_b.tag_id}&query[tags_and]=1')

        self.assertEqual(any_ids, {str(only_a.subscription_id), str(both.subscription_id)})
        self.assertEqual(all_ids, {str(both.subscription_id)})
        self.assertEqual(all_total, 1)

    def test_tags_any_does_not_duplicate_rows(self):
        tag_a = create_test_tag(sport_association=self.sport_association)
        tag_b = create_test_tag(sport_association=self.sport_association)
        both = self.subscription()
        both.tags.add(tag_a, tag_b)

        response, _ = self.list_rows(f'query[tags]={tag_a.tag_id},{tag_b.tag_id}')

        self.assertEqual([row['subscription_id'] for row in response.data['data'].values()],
                         [str(both.subscription_id)])
        self.assertEqual(response.data['meta']['total'], 1)

    def test_status_and_period_filters(self):
        accepted_current = self.subscription(status_flag=Subscription.ACCEPTED, **CURRENT)
        pending_current = self.subscription(status_flag=Subscription.PENDING, **CURRENT)
        past = self.subscription(**PAST)
        future = self.subscription(**FUTURE)

        self.assertEqual(self.ids(f'query[status_flag]={Subscription.PENDING}')[0], {str(pending_current.subscription_id)})
        self.assertEqual(self.ids('query[current_year]=1')[0],
                         {str(accepted_current.subscription_id), str(pending_current.subscription_id)})
        self.assertEqual(self.ids('query[current_year]=0')[0], {str(past.subscription_id)})
        self.assertEqual(self.ids('query[current_year]=2')[0], {str(future.subscription_id)})

    def test_certificate_payment_and_type_filters(self):
        no_medical = self.subscription(type=Subscription.MEMBER_ONLY)
        expired = self.subscription(
            type=Subscription.ASSOCIATE_ONLY,
            medical=create_test_medical_certificate(user=self.user, expiration_date=TODAY - datetime.timedelta(days=1)),
        )
        unpaid = self.subscription(
            medical=create_test_medical_certificate(user=self.user),
            payment=create_test_payment(sport_association=self.sport_association, paid=False),
        )

        self.assertEqual(self.ids('query[certificate_missing_flag]=1')[0], {str(no_medical.subscription_id)})
        self.assertEqual(self.ids('query[certificate_expired_flag]=1')[0], {str(expired.subscription_id)})
        self.assertEqual(self.ids('query[subscription_not_paid_flag]=1')[0], {str(unpaid.subscription_id)})
        self.assertEqual(self.ids('type=athletes')[0], {str(no_medical.subscription_id), str(unpaid.subscription_id)})
        self.assertEqual(self.ids('type=associates')[0], {str(expired.subscription_id), str(unpaid.subscription_id)})
        self.assertEqual(self.ids('query[hide_members]=1')[0], {str(expired.subscription_id), str(unpaid.subscription_id)})

    def test_age_filters_and_lastname_sort(self):
        young = self.subscription(associate=self.associate(last_name='Bianchi', born_date=TODAY.replace(year=TODAY.year - 10)))
        old = self.subscription(associate=self.associate(last_name='Albertini', born_date=TODAY.replace(year=TODAY.year - 40)))

        self.assertEqual(self.ids('query[to_age]=18')[0], {str(young.subscription_id)})
        self.assertEqual(self.ids('query[from_age]=18')[0], {str(old.subscription_id)})
        response, _ = self.list_rows('query[sort_lastname_asc_flag]=1')
        self.assertEqual([row['associate']['last_name'] for row in response.data['data'].values()],
                         ['Albertini', 'Bianchi'])


class SubscriptionListBasicModeTests(SubscriptionListScenarioMixin, TestCase):
    def test_basic_mode_defaults_to_accepted(self):
        family_associate = self.associate(first_name='Anna', last_name='Verdi', family_role='parent')
        accepted = self.subscription(associate=family_associate, **CURRENT)
        pending = self.subscription(status_flag=Subscription.PENDING, **PAST)

        response = self.client.get('/subscription/list?m=1')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['meta'], {})
        self.assertEqual(list(response.data['data'].values()), [{
            'subscription_id': str(accepted.subscription_id),
            'associate': {
                'associate_id': str(family_associate.associate_id),
                'first_name': 'Anna',
                'last_name': 'Verdi',
                'tax_code': family_associate.tax_code,
                'family': None,
                'family_role': 'parent',
            },
            'current_year': True,
        }])
        self.assertNotIn(str(pending.subscription_id), [r['subscription_id'] for r in response.data['data'].values()])

    def test_basic_mode_filter_all_and_specific(self):
        accepted = self.subscription(**CURRENT)
        pending = self.subscription(status_flag=Subscription.PENDING, **PAST)

        all_rows = self.client.get('/subscription/list?m=1&filter=all').data['data'].values()
        pending_rows = self.client.get(f'/subscription/list?m=1&filter={Subscription.PENDING}').data['data'].values()

        self.assertEqual([(r['subscription_id'], r['current_year']) for r in all_rows],
                         [(str(pending.subscription_id), False), (str(accepted.subscription_id), True)])
        self.assertEqual([r['subscription_id'] for r in pending_rows], [str(pending.subscription_id)])


class SubscriptionListArchivedBehaviourTests(SubscriptionListScenarioMixin, TestCase):
    def test_archived_payload(self):
        associate = self.associate()
        create_test_payment(associate=associate, sport_association=self.sport_association,
                            amount=Decimal('10.00'), paid=False)
        create_test_payment(associate=associate, sport_association=self.sport_association,
                            amount=Decimal('5.00'), paid=True)
        medical_document = create_test_document()
        sub = self.subscription(
            associate=associate, archived=True,
            medical=create_test_medical_certificate(user=self.user, document=medical_document),
            **PAST,
        )
        sub.tags.add(create_test_tag(sport_association=self.sport_association, tag_name='Storico'))

        _, rows = self.list_rows(path='/subscription/list/archived')
        row = rows[str(sub.subscription_id)]

        self.assertEqual(row['payments_info'], {'total': Decimal('15.00'), 'to_be_paid': Decimal('10.00')})
        self.assertFalse(row['all_payments_paid'])
        self.assertEqual(row['tags'], [{'tag_id': sub.tags.get().tag_id, 'tag_name': 'Storico'}])
        self.assertEqual(row['medical_document'], medical_document.document_id)
        self.assertEqual(row['associate']['associate_id'], str(associate.associate_id))
        self.assertFalse(row['renewal_available'])
        self.assertTrue(row['current_year'])

    def test_archived_subscription_without_associate(self):
        # Hard-deleting an associate leaves its subscriptions with associate=NULL (on_delete=SET_NULL).
        orphan = self.subscription(archived=True, **PAST)
        orphan.associate.delete()

        _, rows = self.list_rows(path='/subscription/list/archived')
        row = rows[str(orphan.subscription_id)]

        self.assertIsNone(row['associate'])
        self.assertIsNone(row['age'])

    def test_archived_renewal_matches_missing_tax_codes(self):
        # Unlike the paginated list, the archived list has always matched associates
        # without a tax code with each other (tax_code__iexact=None is IS NULL).
        archived = self.subscription(associate=self.associate(tax_code=None), archived=True,
                                     creation_date=timezone.now() - datetime.timedelta(days=730), **PAST)
        self.subscription(associate=self.associate(tax_code=None), **FUTURE)

        _, rows = self.list_rows(path='/subscription/list/archived')

        self.assertFalse(rows[str(archived.subscription_id)]['renewal_available'])

    def test_archived_renewal_available(self):
        # Archived rows derive current_year from creation_date, not from start/end dates.
        created_long_ago = timezone.now() - datetime.timedelta(days=730)
        renewed = self.associate()
        archived_renewed = self.subscription(associate=renewed, archived=True, creation_date=created_long_ago, **PAST)
        self.subscription(associate=renewed, **CURRENT)
        archived_alone = self.subscription(archived=True, creation_date=created_long_ago, **PAST)
        archived_recent = self.subscription(archived=True, **CURRENT)

        _, rows = self.list_rows(path='/subscription/list/archived')

        self.assertFalse(rows[str(archived_renewed.subscription_id)]['renewal_available'])
        self.assertTrue(rows[str(archived_alone.subscription_id)]['renewal_available'])
        self.assertFalse(rows[str(archived_recent.subscription_id)]['renewal_available'])
        self.assertTrue(rows[str(archived_recent.subscription_id)]['current_year'])
        self.assertFalse(rows[str(archived_alone.subscription_id)]['current_year'])


class SubscriptionListAthleteTests(SubscriptionListScenarioMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=self.athlete)

    def test_athlete_payload(self):
        associate = self.associate(user=self.athlete, tax_code='ATHLTE90A01H501X')
        past = self.subscription(associate=associate, user=self.athlete, **PAST)
        current = self.subscription(associate=associate, user=self.athlete, **CURRENT)
        other_association = create_test_sport_association()
        foreign_associate = self.associate(sport_association=other_association, tax_code='ATHLTE90A01H501X')
        by_tax_code = self.subscription(associate=foreign_associate, sport_association=other_association, **PAST)
        carnet = Carnet.objects.create(sport_association=self.sport_association, title='Carnet', lessons_number=5,
                                       fee=Decimal('10.00'))
        CarnetSubscription.objects.create(carnet_id=carnet, subscription=current, meta={})
        course = create_test_course(sport_association=self.sport_association, title='Nuoto')
        course_subscription = create_test_course_subscription(course=course, subscription=current, paid=True)

        response = self.client.get('/subscription/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['meta'], {})
        rows = list(response.data['data'].values())
        self.assertEqual(list(response.data['data']), ['0', '1', '2'])
        self.assertEqual([r['subscription_id'] for r in rows],
                         [str(s.subscription_id) for s in (by_tax_code, current, past)])
        by_id = {r['subscription_id']: r for r in rows}
        past, current, by_tax_code = (str(s.subscription_id) for s in (past, current, by_tax_code))
        self.assertTrue(by_id[current]['carnets'])
        self.assertFalse(by_id[past]['carnets'])
        self.assertEqual(list(by_id[current]['courses']), [{
            'course_subscription_id': course_subscription.course_subscription_id,
            'paid': True,
            'creation_date': course_subscription.creation_date,
            'course__title': 'Nuoto',
            'course__course_id': course.course_id,
            'course__one_fee_payment': course.one_fee_payment,
            'course__one_fee': course.one_fee,
            'one_fee_payment': course_subscription.one_fee_payment,
        }])
        self.assertEqual(list(by_id[past]['courses']), [])
        self.assertEqual(
            {k: by_id[k]['renewal_available'] for k in by_id},
            {past: False, current: False, by_tax_code: True},
        )
        self.assertTrue(by_id[current]['is_current'])
        self.assertEqual(by_id[current]['associate']['associate_id'], str(associate.associate_id))
        self.assertEqual(by_id[current]['sport_association']['sport_association_id'],
                         str(self.sport_association.sport_association_id))


class SubscriptionListQueryCountTests(SubscriptionListScenarioMixin, TestCase):
    """The number of queries must not grow with the number of rows."""

    def add_rows(self, count, archived=False, user=None):
        for _ in range(count):
            associate = self.associate(user=user)
            create_test_payment(associate=associate, sport_association=self.sport_association, paid=False)
            sub = self.subscription(
                associate=associate, archived=archived, user=user,
                medical=create_test_medical_certificate(user=self.user, document=create_test_document()),
                document_pdf=create_test_document(),
                **PAST,
            )
            sub.tags.add(create_test_tag(sport_association=self.sport_association))
            if user is not None:
                CarnetSubscription.objects.create(
                    carnet_id=Carnet.objects.create(sport_association=self.sport_association, title='C',
                                                    lessons_number=1, fee=Decimal('1.00')),
                    subscription=sub, meta={},
                )
                create_test_course_subscription(
                    course=create_test_course(sport_association=self.sport_association), subscription=sub,
                )

    def assert_constant_queries(self, path, **rows_kwargs):
        self.add_rows(2, **rows_kwargs)
        small = self.count_queries(path)
        self.add_rows(6, **rows_kwargs)
        large = self.count_queries(path)
        self.assertEqual(small, large, f'{path}: {small} queries for 2 rows, {large} for 8 rows')

    def test_list_query_count(self):
        self.assert_constant_queries('/subscription/list?pagination[perpage]=50')

    def test_list_basic_mode_query_count(self):
        self.assert_constant_queries('/subscription/list?m=1&filter=all')

    def test_list_archived_query_count(self):
        self.assert_constant_queries('/subscription/list/archived?pagination[perpage]=50', archived=True)

    def test_list_athlete_query_count(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        self.assert_constant_queries('/subscription/list', user=athlete)
