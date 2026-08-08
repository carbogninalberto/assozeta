"""
Tests for Audit Resolvers - sport_association resolver functions.

Ported to self-host (assozeta): adapted imports to use self-host test factories
from ``application.tests.fixtures.factories``.
"""
import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.contrib.contenttypes.models import ContentType

from application.models import User, Course, CourseSubscription
from application.models.payment_models import Payment, PaymentCategory
from application.models.subscriptions_models import (
    Subscription, MedicalCertificate, MedicalAppointments, Signature
)
from application.models.user_models import (
    SportAssociation, Associate, Instructor, InstructorHours,
    AssociateTutorRelation
)
from application.models.courses_models import (
    CourseSubscriptionInstallment, CampsAndRetreats, CampsAndRetreatsPeriod,
    CampsAndRetreatsPeriodsService, CampsAndRetreatsSubscription,
    CampsAndRetreatsSubscriptionPeriod
)
from application.models.invoices_models import Invoice, InvoiceRows
from application.models.attendee_models import AttendanceRegistry, AttendanceDay
from application.models.modules_models import Module, ModuleResponses
from application.models.carnet_models import Carnet, CarnetSubscription
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_associate,
    create_test_subscription
)

from application.audit_resolvers import (
    register_resolver, get_resolver,
    direct_resolver, sport_association_self_resolver,
    course_subscription_resolver, course_subscription_installment_resolver,
    instructor_hours_resolver, medical_certificate_resolver,
    medical_appointments_resolver, subscription_related_resolver,
    signature_resolver, camps_period_resolver, camps_service_resolver,
    camps_subscription_resolver, camps_subscription_period_resolver,
    invoice_rows_resolver, attendance_registry_resolver,
    attendance_day_resolver, module_responses_resolver,
    carnet_subscription_resolver, user_resolver,
    associate_tutor_relation_resolver, setup_audit_resolvers,
    SPORT_ASSOCIATION_RESOLVERS
)


class BaseResolverTestCase(TestCase):
    """Base test case with common setup for resolver tests."""

    def setUp(self):
        """Set up common test fixtures."""
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(
            sport_association=self.sport_association,
            user=self.user
        )


class RegisterResolverTests(TestCase):
    """Tests for register_resolver and get_resolver functions."""

    def test_register_resolver_success(self):
        """Test registering a resolver for a model."""
        test_resolver = lambda obj: (obj.sport_association, 'test') if obj.sport_association else None
        register_resolver(Payment, test_resolver)

        content_type = ContentType.objects.get_for_model(Payment)
        key = f"{content_type.app_label}.{content_type.model}"
        self.assertIn(key, SPORT_ASSOCIATION_RESOLVERS)

    def test_get_resolver_returns_function(self):
        """Test getting a registered resolver."""
        content_type = ContentType.objects.get_for_model(Payment)
        resolver = get_resolver(content_type)

    def test_get_resolver_nonexistent_returns_none(self):
        """Test getting resolver for unregistered model returns None."""
        mock_content_type = MagicMock()
        mock_content_type.app_label = 'nonexistent'
        mock_content_type.model = 'fakemodel'

        resolver = get_resolver(mock_content_type)
        self.assertIsNone(resolver)

    @patch('application.audit_resolvers.logger')
    def test_register_resolver_exception_logs_warning(self, mock_logger):
        """Test that registration exception logs warning."""
        with patch('application.audit_resolvers.ContentType.objects.get_for_model') as mock_get:
            mock_get.side_effect = Exception("Test error")
            register_resolver(MagicMock, lambda x: None)
            mock_logger.warning.assert_called()


class DirectResolverTests(BaseResolverTestCase):
    """Tests for direct_resolver function."""

    def test_direct_resolver_with_sport_association(self):
        """Test direct_resolver returns sport_association for models with FK."""
        result = direct_resolver(self.associate)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)
        self.assertEqual(result[1], 'direct')

    def test_direct_resolver_without_sport_association(self):
        """Test direct_resolver returns None when no sport_association."""
        mock_obj = MagicMock(spec=[])

        result = direct_resolver(mock_obj)
        self.assertIsNone(result)

    def test_direct_resolver_with_none_sport_association(self):
        """Test direct_resolver returns None when sport_association is None."""
        mock_obj = MagicMock()
        mock_obj.sport_association = None

        result = direct_resolver(mock_obj)
        self.assertIsNone(result)


class SportAssociationSelfResolverTests(BaseResolverTestCase):
    """Tests for sport_association_self_resolver function."""

    def test_self_resolver_returns_itself(self):
        """Test self resolver returns the sport_association itself."""
        result = sport_association_self_resolver(self.sport_association)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)
        self.assertEqual(result[1], 'self')


class CourseSubscriptionResolverTests(BaseResolverTestCase):
    """Tests for course_subscription_resolver function."""

    def test_resolver_with_course(self):
        """Test resolver returns sport_association from course."""
        course = Course.objects.create(
            sport_association=self.sport_association,
            title='Test Course',
            description='Test'
        )
        course_sub = CourseSubscription.objects.create(
            course=course,
            subscription=self.subscription
        )

        result = course_subscription_resolver(course_sub)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)
        self.assertEqual(result[1], 'course.sport_association')

    def test_resolver_without_course(self):
        """Test resolver returns None when no course."""
        mock_obj = MagicMock()
        mock_obj.course = None

        result = course_subscription_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_course_without_sport_association(self):
        """Test resolver returns None when course has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.course = MagicMock()
        mock_obj.course.sport_association = None

        result = course_subscription_resolver(mock_obj)
        self.assertIsNone(result)


class CourseSubscriptionInstallmentResolverTests(BaseResolverTestCase):
    """Tests for course_subscription_installment_resolver function."""

    def test_resolver_with_valid_chain(self):
        """Test resolver follows the full chain to sport_association."""
        course = Course.objects.create(
            sport_association=self.sport_association,
            title='Test Course',
            description='Test'
        )
        course_sub = CourseSubscription.objects.create(
            course=course,
            subscription=self.subscription
        )
        mock_installment = MagicMock()
        mock_installment.course_subscription = course_sub

        result = course_subscription_installment_resolver(mock_installment)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_course_subscription(self):
        """Test resolver returns None when no course_subscription."""
        mock_obj = MagicMock()
        mock_obj.course_subscription = None

        result = course_subscription_installment_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_without_course(self):
        """Test resolver returns None when course_subscription has no course."""
        mock_obj = MagicMock()
        mock_obj.course_subscription = MagicMock()
        mock_obj.course_subscription.course = None

        result = course_subscription_installment_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_course_without_sport_association(self):
        """Test resolver returns None when course has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.course_subscription = MagicMock()
        mock_obj.course_subscription.course = MagicMock()
        mock_obj.course_subscription.course.sport_association = None

        result = course_subscription_installment_resolver(mock_obj)
        self.assertIsNone(result)


class InstructorHoursResolverTests(BaseResolverTestCase):
    """Tests for instructor_hours_resolver function."""

    def test_resolver_with_instructor(self):
        """Test resolver returns sport_association from instructor."""
        instructor = Instructor.objects.create(
            user=self.user,
            first_name='Test',
            last_name='Instructor'
        )
        mock_hours = MagicMock()
        mock_instructor = MagicMock()
        mock_instructor.sport_association = self.sport_association
        mock_hours.instructor = mock_instructor

        result = instructor_hours_resolver(mock_hours)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_instructor(self):
        """Test resolver returns None when no instructor."""
        mock_obj = MagicMock(spec=[])

        result = instructor_hours_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_instructor_without_sport_association(self):
        """Test resolver returns None when instructor has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.instructor = MagicMock(spec=[])

        result = instructor_hours_resolver(mock_obj)
        self.assertIsNone(result)


class MedicalCertificateResolverTests(BaseResolverTestCase):
    """Tests for medical_certificate_resolver function."""

    def test_resolver_with_subscription(self):
        """Test resolver returns sport_association from subscription."""
        mock_cert = MagicMock()
        mock_cert.subscription = self.subscription

        result = medical_certificate_resolver(mock_cert)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_subscription(self):
        """Test resolver returns None when no subscription."""
        mock_obj = MagicMock(spec=[])

        result = medical_certificate_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_subscription_without_sport_association(self):
        """Test resolver returns None when subscription has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.subscription = MagicMock()
        mock_obj.subscription.sport_association = None

        result = medical_certificate_resolver(mock_obj)
        self.assertIsNone(result)


class MedicalAppointmentsResolverTests(BaseResolverTestCase):
    """Tests for medical_appointments_resolver function."""

    def test_resolver_with_subscription(self):
        """Test resolver returns sport_association from subscription."""
        mock_appointment = MagicMock()
        mock_appointment.subscription = self.subscription

        result = medical_appointments_resolver(mock_appointment)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_subscription(self):
        """Test resolver returns None when no subscription."""
        mock_obj = MagicMock(spec=[])

        result = medical_appointments_resolver(mock_obj)
        self.assertIsNone(result)


class SubscriptionRelatedResolverTests(BaseResolverTestCase):
    """Tests for subscription_related_resolver function."""

    def test_resolver_with_subscription(self):
        """Test resolver returns sport_association from subscription."""
        mock_obj = MagicMock()
        mock_obj.subscription = self.subscription

        result = subscription_related_resolver(mock_obj)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_subscription(self):
        """Test resolver returns None when no subscription."""
        mock_obj = MagicMock(spec=[])

        result = subscription_related_resolver(mock_obj)
        self.assertIsNone(result)


class SignatureResolverTests(BaseResolverTestCase):
    """Tests for signature_resolver function."""

    def test_resolver_with_subscription(self):
        """Test resolver returns sport_association from subscription."""
        mock_sig = MagicMock()
        mock_sig.subscription = self.subscription

        result = signature_resolver(mock_sig)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_subscription(self):
        """Test resolver returns None when no subscription."""
        mock_obj = MagicMock(spec=[])

        result = signature_resolver(mock_obj)
        self.assertIsNone(result)


class CampsPeriodResolverTests(BaseResolverTestCase):
    """Tests for camps_period_resolver function."""

    def test_resolver_with_camps(self):
        """Test resolver returns sport_association from camps."""
        camps = CampsAndRetreats.objects.create(
            sport_association=self.sport_association,
            title='Test Camp'
        )
        mock_period = MagicMock()
        mock_period.camps_and_retreats = camps

        result = camps_period_resolver(mock_period)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_camps(self):
        """Test resolver returns None when no camps."""
        mock_obj = MagicMock(spec=[])

        result = camps_period_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_camps_without_sport_association(self):
        """Test resolver returns None when camps has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.camps_and_retreats = MagicMock()
        mock_obj.camps_and_retreats.sport_association = None

        result = camps_period_resolver(mock_obj)
        self.assertIsNone(result)


class CampsServiceResolverTests(BaseResolverTestCase):
    """Tests for camps_service_resolver function."""

    def test_resolver_with_full_chain(self):
        """Test resolver follows full chain to sport_association."""
        camps = CampsAndRetreats.objects.create(
            sport_association=self.sport_association,
            title='Test Camp'
        )
        mock_period = MagicMock()
        mock_period.camps_and_retreats = camps
        mock_service = MagicMock()
        mock_service.camps_and_retreats_period = mock_period

        result = camps_service_resolver(mock_service)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_period(self):
        """Test resolver returns None when no period."""
        mock_obj = MagicMock(spec=[])

        result = camps_service_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_period_without_camps(self):
        """Test resolver returns None when period has no camps."""
        mock_obj = MagicMock()
        mock_obj.camps_and_retreats_period = MagicMock(spec=[])

        result = camps_service_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_camps_without_sport_association(self):
        """Test resolver returns None when camps has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.camps_and_retreats_period = MagicMock()
        mock_obj.camps_and_retreats_period.camps_and_retreats = MagicMock()
        mock_obj.camps_and_retreats_period.camps_and_retreats.sport_association = None

        result = camps_service_resolver(mock_obj)
        self.assertIsNone(result)


class CampsSubscriptionResolverTests(BaseResolverTestCase):
    """Tests for camps_subscription_resolver function."""

    def test_resolver_with_camps(self):
        """Test resolver returns sport_association from camps."""
        camps = CampsAndRetreats.objects.create(
            sport_association=self.sport_association,
            title='Test Camp'
        )
        mock_sub = MagicMock()
        mock_sub.camps_and_retreats = camps

        result = camps_subscription_resolver(mock_sub)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_camps(self):
        """Test resolver returns None when no camps."""
        mock_obj = MagicMock(spec=[])

        result = camps_subscription_resolver(mock_obj)
        self.assertIsNone(result)


class CampsSubscriptionPeriodResolverTests(BaseResolverTestCase):
    """Tests for camps_subscription_period_resolver function."""

    def test_resolver_with_full_chain(self):
        """Test resolver follows full chain to sport_association."""
        camps = CampsAndRetreats.objects.create(
            sport_association=self.sport_association,
            title='Test Camp'
        )
        mock_camps_sub = MagicMock()
        mock_camps_sub.camps_and_retreats = camps
        mock_period = MagicMock()
        mock_period.camps_and_retreats_subscription = mock_camps_sub

        result = camps_subscription_period_resolver(mock_period)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_subscription(self):
        """Test resolver returns None when no subscription."""
        mock_obj = MagicMock(spec=[])

        result = camps_subscription_period_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_subscription_without_camps(self):
        """Test resolver returns None when subscription has no camps."""
        mock_obj = MagicMock()
        mock_obj.camps_and_retreats_subscription = MagicMock(spec=[])

        result = camps_subscription_period_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_camps_without_sport_association(self):
        """Test resolver returns None when camps has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.camps_and_retreats_subscription = MagicMock()
        mock_obj.camps_and_retreats_subscription.camps_and_retreats = MagicMock()
        mock_obj.camps_and_retreats_subscription.camps_and_retreats.sport_association = None

        result = camps_subscription_period_resolver(mock_obj)
        self.assertIsNone(result)


class InvoiceRowsResolverTests(BaseResolverTestCase):
    """Tests for invoice_rows_resolver function."""

    def test_resolver_with_invoice(self):
        """Test resolver returns sport_association from invoice."""
        invoice = Invoice.objects.create(
            sport_association=self.sport_association,
            number=1,
            membership_fee=Decimal('50.00'),
            activity_fee=Decimal('100.00')
        )
        mock_row = MagicMock()
        mock_row.invoice = invoice

        result = invoice_rows_resolver(mock_row)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_invoice(self):
        """Test resolver returns None when no invoice."""
        mock_obj = MagicMock(spec=[])

        result = invoice_rows_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_invoice_without_sport_association(self):
        """Test resolver returns None when invoice has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.invoice = MagicMock()
        mock_obj.invoice.sport_association = None

        result = invoice_rows_resolver(mock_obj)
        self.assertIsNone(result)


class AttendanceRegistryResolverTests(BaseResolverTestCase):
    """Tests for attendance_registry_resolver function."""

    def test_resolver_with_course(self):
        """Test resolver returns sport_association from course."""
        course = Course.objects.create(
            sport_association=self.sport_association,
            title='Test Course',
            description='Test'
        )
        mock_registry = MagicMock()
        mock_registry.course = course

        result = attendance_registry_resolver(mock_registry)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_course(self):
        """Test resolver returns None when no course."""
        mock_obj = MagicMock(spec=[])

        result = attendance_registry_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_course_without_sport_association(self):
        """Test resolver returns None when course has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.course = MagicMock()
        mock_obj.course.sport_association = None

        result = attendance_registry_resolver(mock_obj)
        self.assertIsNone(result)


class AttendanceDayResolverTests(BaseResolverTestCase):
    """Tests for attendance_day_resolver function."""

    def test_resolver_with_full_chain(self):
        """Test resolver follows full chain to sport_association."""
        course = Course.objects.create(
            sport_association=self.sport_association,
            title='Test Course',
            description='Test'
        )
        mock_day = MagicMock()
        mock_day.attendance_registry = MagicMock()
        mock_day.attendance_registry.course = course

        result = attendance_day_resolver(mock_day)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_registry(self):
        """Test resolver returns None when no registry."""
        mock_obj = MagicMock(spec=[])

        result = attendance_day_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_registry_without_course(self):
        """Test resolver returns None when registry has no course."""
        mock_obj = MagicMock()
        mock_obj.attendance_registry = MagicMock(spec=[])

        result = attendance_day_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_course_without_sport_association(self):
        """Test resolver returns None when course has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.attendance_registry = MagicMock()
        mock_obj.attendance_registry.course = MagicMock()
        mock_obj.attendance_registry.course.sport_association = None

        result = attendance_day_resolver(mock_obj)
        self.assertIsNone(result)


class ModuleResponsesResolverTests(BaseResolverTestCase):
    """Tests for module_responses_resolver function."""

    def test_resolver_with_module(self):
        """Test resolver returns sport_association from module."""
        module = Module.objects.create(
            sport_association=self.sport_association,
            title='Test Module'
        )
        mock_response = MagicMock()
        mock_response.module = module

        result = module_responses_resolver(mock_response)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_module(self):
        """Test resolver returns None when no module."""
        mock_obj = MagicMock(spec=[])

        result = module_responses_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_module_without_sport_association(self):
        """Test resolver returns None when module has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.module = MagicMock()
        mock_obj.module.sport_association = None

        result = module_responses_resolver(mock_obj)
        self.assertIsNone(result)


class CarnetSubscriptionResolverTests(BaseResolverTestCase):
    """Tests for carnet_subscription_resolver function."""

    def test_resolver_with_carnet(self):
        """Test resolver returns sport_association from carnet."""
        carnet = Carnet.objects.create(
            sport_association=self.sport_association,
            title='Test Carnet'
        )
        mock_sub = MagicMock()
        mock_sub.carnet = carnet
        mock_sub.subscription = None

        result = carnet_subscription_resolver(mock_sub)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_with_subscription_fallback(self):
        """Test resolver falls back to subscription when carnet has no sport_association."""
        mock_sub = MagicMock()
        mock_sub.carnet = MagicMock()
        mock_sub.carnet.sport_association = None
        mock_sub.subscription = self.subscription

        result = carnet_subscription_resolver(mock_sub)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_carnet_or_subscription(self):
        """Test resolver returns None when no carnet or subscription."""
        mock_obj = MagicMock(spec=[])

        result = carnet_subscription_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_carnet_without_sport_association_no_subscription(self):
        """Test resolver returns None when carnet has no sport_association and no subscription."""
        mock_obj = MagicMock()
        mock_obj.carnet = MagicMock()
        mock_obj.carnet.sport_association = None
        del mock_obj.subscription

        result = carnet_subscription_resolver(mock_obj)
        self.assertIsNone(result)


class UserResolverTests(BaseResolverTestCase):
    """Tests for user_resolver function."""

    def test_resolver_with_association_user(self):
        """Test resolver returns sport_association for association user."""
        result = user_resolver(self.user)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_sport_association(self):
        """Test resolver returns None for user without sport_association."""
        athlete = create_test_user(role=User.ATHLETE)

        result = user_resolver(athlete)

        self.assertIsNone(result)

    def test_resolver_handles_exception(self):
        """Test resolver handles exceptions gracefully."""
        with patch('application.models.user_models.SportAssociation.objects.filter') as mock_filter:
            mock_filter.side_effect = Exception("Database error")
            mock_user = MagicMock()

            result = user_resolver(mock_user)

            self.assertIsNone(result)


class AssociateTutorRelationResolverTests(BaseResolverTestCase):
    """Tests for associate_tutor_relation_resolver function."""

    def test_resolver_with_associate(self):
        """Test resolver returns sport_association from associate."""
        mock_relation = MagicMock()
        mock_relation.associate = self.associate

        result = associate_tutor_relation_resolver(mock_relation)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.sport_association)

    def test_resolver_without_associate(self):
        """Test resolver returns None when no associate."""
        mock_obj = MagicMock(spec=[])

        result = associate_tutor_relation_resolver(mock_obj)
        self.assertIsNone(result)

    def test_resolver_associate_without_sport_association(self):
        """Test resolver returns None when associate has no sport_association."""
        mock_obj = MagicMock()
        mock_obj.associate = MagicMock()
        mock_obj.associate.sport_association = None

        result = associate_tutor_relation_resolver(mock_obj)
        self.assertIsNone(result)


class SetupAuditResolversTests(TestCase):
    """Tests for setup_audit_resolvers function."""

    def test_setup_registers_resolvers(self):
        """Test that setup registers all expected resolvers."""
        setup_audit_resolvers()

        self.assertGreater(len(SPORT_ASSOCIATION_RESOLVERS), 0)

    def test_setup_logs_count(self):
        """Test that setup logs the resolver count."""
        with patch('application.audit_resolvers.logger') as mock_logger:
            setup_audit_resolvers()
            mock_logger.info.assert_called()
