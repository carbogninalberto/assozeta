import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from application.models.courses_models import Course, CourseSubscription
from application.models.payment_models import Payment
from application.models.subscriptions_models import Subscription
from application.models.user_models import Associate
from application.serializers.user_serializers import AssociateSerializer
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_course,
    create_test_payment_category,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)
from application.utils.subscriptions_utils import (
    add_course_subscription,
    add_course_to_subscription,
    check_if_subscription_exists,
    extract_the_membership_plan,
    extract_the_subscription_plan,
    fill_data_preregisration,
    get_controlled_searchable_fields,
    get_date_variations,
    get_membership_fee,
    get_subscription_fee,
    safe_int,
    sqlite_search,
)


class SubscriptionUtilsTestCase(TestCase):
    def setUp(self):
        self.user = create_test_user()
        self.sport_association = create_test_sport_association(user=self.user)
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.payment_category = create_test_payment_category(sport_association=self.sport_association)
        self.user.default_payment_category = self.payment_category
        self.user.default_payment_category_courses = self.payment_category
        self.user.save()


class MembershipFeeTests(SubscriptionUtilsTestCase):
    def test_associate_only_has_no_membership_fee(self):
        fee, meta = get_membership_fee({}, self.sport_association, Subscription.ASSOCIATE_ONLY)

        self.assertIsNone(fee)
        self.assertIsNone(meta)

    def test_member_only_uses_single_fee(self):
        self.sport_association.multiple_membership_fee = False
        self.sport_association.membership_fee = Decimal('50.00')
        self.sport_association.save()

        fee, meta = get_membership_fee({}, self.sport_association, Subscription.MEMBER_ONLY)

        self.assertEqual(fee, 50.0)
        self.assertIsNone(meta)

    def test_associate_and_member_includes_payment_category(self):
        self.sport_association.multiple_membership_fee = False
        self.sport_association.membership_fee = Decimal('75.00')
        self.sport_association.save()

        fee, meta = get_membership_fee({}, self.sport_association, Subscription.ASSOCIATE_AND_MEMBER)

        self.assertEqual(fee, 75.0)
        self.assertEqual(meta[0]['payment_category_id'], str(self.payment_category.payment_category_id))

    def test_multiple_fees_require_plan_id(self):
        self.sport_association.multiple_membership_fee = True
        self.sport_association.membership_fee_plans = [
            {'id': 'standard', 'name': 'Standard', 'membership_fee': 100},
        ]
        self.sport_association.save()

        with self.assertRaises(ValidationError):
            get_membership_fee({}, self.sport_association, Subscription.ASSOCIATE_AND_MEMBER)

    def test_multiple_fees_use_selected_plan(self):
        plan_id = str(uuid.uuid4())
        self.sport_association.multiple_membership_fee = True
        self.sport_association.membership_fee_plans = [
            {'id': plan_id, 'name': 'Gold', 'membership_fee': 150},
        ]
        self.sport_association.save()

        fee, meta = get_membership_fee(
            {'membership_plan_id': plan_id},
            self.sport_association,
            Subscription.ASSOCIATE_AND_MEMBER,
        )

        self.assertEqual(fee, 150.0)
        self.assertEqual(meta[0]['id'], plan_id)

    def test_missing_single_membership_fee_returns_none(self):
        self.sport_association.multiple_membership_fee = False
        self.sport_association.membership_fee = None
        self.sport_association.save()

        fee, meta = get_membership_fee({}, self.sport_association, Subscription.MEMBER_ONLY)

        self.assertIsNone(fee)
        self.assertIsNone(meta)


class SubscriptionFeeTests(SubscriptionUtilsTestCase):
    def test_single_subscription_fee(self):
        fee, meta = get_subscription_fee({}, self.sport_association, Subscription.ASSOCIATE_ONLY)

        self.assertEqual(fee, Decimal('100.00'))
        self.assertIsNone(meta)

    def test_multiple_subscription_fee_uses_plan(self):
        plan_id = str(uuid.uuid4())
        self.sport_association.multiple_subscription_fee = True
        self.sport_association.subscription_fee_plans = [
            {'id': plan_id, 'name': 'Standard', 'subscription_fee': 200},
        ]
        self.sport_association.save()

        fee, meta = get_subscription_fee(
            {'plan_id': plan_id},
            self.sport_association,
            Subscription.ASSOCIATE_ONLY,
        )

        self.assertEqual(fee, 200)
        self.assertEqual(meta['subscription_data']['id'], plan_id)

    def test_member_only_has_zero_subscription_fee(self):
        fee, meta = get_subscription_fee({}, self.sport_association, Subscription.MEMBER_ONLY)

        self.assertEqual(fee, 0)
        self.assertIsNone(meta)


class SubscriptionPlanExtractionTests(SubscriptionUtilsTestCase):
    def test_extracts_subscription_plan(self):
        self.sport_association.subscription_fee_plans = [
            {'id': 'plan-1', 'name': 'Plan 1', 'subscription_fee': 100},
        ]

        self.assertEqual(
            extract_the_subscription_plan('plan-1', self.sport_association)['name'],
            'Plan 1',
        )
        self.assertIsNone(extract_the_subscription_plan('missing', self.sport_association))
        self.assertIsNone(extract_the_subscription_plan(None, self.sport_association))

    def test_extracts_membership_plan(self):
        self.sport_association.membership_fee_plans = [
            {'id': 'plan-1', 'name': 'Gold', 'membership_fee': 200},
        ]
        self.sport_association.save()

        self.assertEqual(
            extract_the_membership_plan('plan-1', self.sport_association)['name'],
            'Gold',
        )
        self.assertIsNone(extract_the_membership_plan('missing', self.sport_association))


class SubscriptionDateAndIntegerTests(TestCase):
    def test_date_variations_for_supported_separators(self):
        cases = [
            ('15.03.2024', '2024-03-15'),
            ('15/03/2024', '15.03.2024'),
            ('15-03-2024', '2024-03-15'),
            ('15.03.24', '2024'),
        ]
        for value, expected_part in cases:
            with self.subTest(value=value):
                variations = get_date_variations(value)
                self.assertTrue(any(expected_part in variation for variation in variations))

    def test_non_date_has_no_variations(self):
        self.assertEqual(get_date_variations('not a date'), [])

    def test_safe_int(self):
        cases = [
            (42, 0, 42),
            (' 123 ', 0, 123),
            (None, 10, 10),
            ('invalid', 0, 0),
            ('12.5', 0, 0),
            ('', 0, 0),
        ]
        for value, default, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(safe_int(value, default), expected)

    def test_safe_int_reads_subscription_number(self):
        class SubscriptionNumber:
            subscription_number = '12345'

        self.assertEqual(safe_int(SubscriptionNumber()), 12345)


class SubscriptionSearchTests(SubscriptionUtilsTestCase):
    def test_empty_search_returns_queryset(self):
        create_test_subscription(sport_association=self.sport_association)
        queryset = Subscription.objects.filter(sport_association=self.sport_association)

        result = sqlite_search(queryset, '')

        self.assertEqual(result.count(), queryset.count())

    def test_searches_associate_fields(self):
        associate = create_test_associate(
            sport_association=self.sport_association,
            first_name='UniqueFirst',
            last_name='UniqueLast',
            tax_code='SEARCHABLETAX1234',
        )
        create_test_subscription(sport_association=self.sport_association, associate=associate)
        queryset = Subscription.objects.filter(sport_association=self.sport_association)

        for term in ('UniqueFirst', 'UniqueLast', 'SEARCHABLETAX'):
            with self.subTest(term=term):
                self.assertEqual(sqlite_search(queryset, term).count(), 1)

    def test_controlled_searchable_fields(self):
        subscription_fields = get_controlled_searchable_fields(Subscription)
        payment_fields = get_controlled_searchable_fields(Payment)
        associate_fields = get_controlled_searchable_fields(Associate)

        self.assertIn('subscription_number', subscription_fields)
        self.assertTrue(any(field == 'associate__first_name' for field in subscription_fields))
        self.assertIn('amount', payment_fields)
        self.assertIn('first_name', associate_fields)


class SubscriptionPreregistrationTests(SubscriptionUtilsTestCase):
    def test_fills_data_from_subscription(self):
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
        )
        data = {
            'subscription_id': str(subscription.subscription_id),
            'associate_data': {'type': Subscription.ASSOCIATE_AND_MEMBER},
        }

        result, user = fill_data_preregisration(data)

        self.assertEqual(result['associate_data']['first_name'], self.associate.first_name)
        self.assertEqual(user, subscription.user)

    def test_missing_subscription_raises(self):
        with self.assertRaises(ValidationError):
            fill_data_preregisration({
                'subscription_id': str(uuid.uuid4()),
                'associate_data': {'type': Subscription.ASSOCIATE_ONLY},
            })


class SubscriptionExistenceTests(SubscriptionUtilsTestCase):
    def test_returns_false_for_new_associate(self):
        serializer = AssociateSerializer(data={
            'first_name': 'New',
            'last_name': 'Person',
            'tax_code': 'NEWPERSON123456',
            'born_date': '1990-01-01',
            'born_city': 'Roma',
            'sex': 'M',
        })

        result = check_if_subscription_exists(
            self.sport_association,
            serializer,
            date.today(),
            date.today() + timedelta(days=365),
            {},
        )

        self.assertFalse(result)


class AddCourseToSubscriptionTests(SubscriptionUtilsTestCase):
    def setUp(self):
        super().setUp()
        self.subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
        )
        self.course = create_test_course(
            sport_association=self.sport_association,
            fee=Decimal('75.00'),
            multi_payments=False,
        )

    def test_creates_course_subscription_and_payment(self):
        result = add_course_to_subscription(
            self.course,
            self.subscription,
            self.sport_association,
        )

        self.assertIsNotNone(result['payment'])
        self.assertTrue(CourseSubscription.objects.filter(
            course=self.course,
            subscription=self.subscription,
        ).exists())

    def test_duplicate_returns_bad_request(self):
        add_course_to_subscription(self.course, self.subscription, self.sport_association)

        response = add_course_to_subscription(self.course, self.subscription, self.sport_association)

        self.assertEqual(response.status_code, 400)

    def test_one_fee_payment_uses_one_fee(self):
        self.course.one_fee = Decimal('200.00')
        self.course.save()

        result = add_course_to_subscription(
            self.course,
            self.subscription,
            self.sport_association,
            {'one_fee_payment': True},
        )

        payment = Payment.objects.get(payment_id=result['payment']['payment_id'])
        self.assertEqual(payment.amount, Decimal('200.00'))


class AddCourseSubscriptionTests(SubscriptionUtilsTestCase):
    def setUp(self):
        super().setUp()
        self.subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
        )
        self.course = create_test_course(
            sport_association=self.sport_association,
            fee=Decimal('50.00'),
            one_fee=Decimal('0.00'),
            multi_payments=False,
            course_type=Course.DEFAULT_TYPE,
        )
        self.course_subscription = CourseSubscription.objects.create(
            subscription=self.subscription,
            course=self.course,
        )

    def test_regular_fee_creates_payment_with_course_metadata(self):
        result = add_course_subscription(self.course_subscription, self.sport_association)

        self.assertEqual(result.payment.amount, Decimal('50.00'))
        self.assertEqual(result.payment.course['value'], str(self.course.course_id))
        self.assertEqual(result.payment.meta['course_title'], self.course.title)

    def test_one_fee_creates_single_payment(self):
        self.course.one_fee = Decimal('150.00')
        self.course.save()

        result = add_course_subscription(
            self.course_subscription,
            self.sport_association,
            {'one_fee_payment': True},
        )

        self.assertEqual(result.payment.amount, Decimal('150.00'))
        self.assertEqual(result.payment.meta['one_fee'], 'true')

    def test_multi_payment_course_sets_flag_without_payment(self):
        self.course.multi_payments = True
        self.course.save()

        result = add_course_subscription(self.course_subscription, self.sport_association)

        self.assertTrue(result.multi_payments)
        self.assertIsNone(result.payment)

    def test_zero_fee_creates_no_payment(self):
        self.course.fee = Decimal('0.00')
        self.course.save()

        result = add_course_subscription(self.course_subscription, self.sport_association)

        self.assertIsNone(result.payment)

    def test_multiple_quote_creates_selected_payment(self):
        self.course.course_type = Course.MULTIPLE_QUOTES_TYPE
        self.course.save()

        result = add_course_subscription(
            self.course_subscription,
            self.sport_association,
            {'multiple_quote': {'amount': '100,50', 'title': 'Quarterly'}},
        )

        self.assertEqual(result.payment.amount, Decimal('100.50'))
        self.assertEqual(result.payment.meta['multiple_quote']['title'], 'Quarterly')
