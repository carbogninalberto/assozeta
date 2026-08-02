"""
Copyright: Bakney S.r.l.
Audit log sport_association resolvers for all registered models.

This module provides resolver functions that determine the SportAssociation
for each audited model type. Used by the signal handler to create AuditLogIndex entries.
"""
import logging

from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger(__name__)

# Registry mapping content type keys to resolver functions
SPORT_ASSOCIATION_RESOLVERS = {}


def register_resolver(model_class, resolver_func):
    """
    Register a resolver function for a model class.

    Args:
        model_class: The Django model class
        resolver_func: Function that takes an object and returns (sport_association, resolution_path) or None
    """
    try:
        content_type = ContentType.objects.get_for_model(model_class)
        key = f"{content_type.app_label}.{content_type.model}"
        SPORT_ASSOCIATION_RESOLVERS[key] = resolver_func
        logger.debug(f"Registered audit resolver for {key}")
    except Exception as e:
        logger.warning(f"Could not register resolver for {model_class}: {e}")


def get_resolver(content_type):
    """Get the resolver function for a content type."""
    key = f"{content_type.app_label}.{content_type.model}"
    return SPORT_ASSOCIATION_RESOLVERS.get(key)


# =============================================================================
# RESOLVER FUNCTIONS
# =============================================================================

def direct_resolver(obj):
    """
    Resolver for models with direct sport_association FK.
    Works for most models in the system.
    """
    if hasattr(obj, 'sport_association') and obj.sport_association:
        return obj.sport_association, 'direct'
    return None


def sport_association_self_resolver(obj):
    """SportAssociation IS the sport_association."""
    return obj, 'self'


def course_subscription_resolver(obj):
    """CourseSubscription -> Course -> sport_association"""
    if obj.course and obj.course.sport_association:
        return obj.course.sport_association, 'course.sport_association'
    return None


def course_subscription_installment_resolver(obj):
    """CourseSubscriptionInstallment -> CourseSubscription -> Course -> sport_association"""
    if obj.course_subscription and obj.course_subscription.course:
        course = obj.course_subscription.course
        if course.sport_association:
            return course.sport_association, 'course_subscription.course.sport_association'
    return None


def instructor_hours_resolver(obj):
    """InstructorHours -> Instructor -> sport_association (if direct) or via courses"""
    if hasattr(obj, 'instructor') and obj.instructor:
        if hasattr(obj.instructor, 'sport_association') and obj.instructor.sport_association:
            return obj.instructor.sport_association, 'instructor.sport_association'
    return None


def medical_certificate_resolver(obj):
    """MedicalCertificate -> through subscription or user"""
    # Try via subscription
    if hasattr(obj, 'subscription') and obj.subscription:
        if obj.subscription.sport_association:
            return obj.subscription.sport_association, 'subscription.sport_association'
    return None


def medical_appointments_resolver(obj):
    """MedicalAppointments -> Subscription -> sport_association"""
    if hasattr(obj, 'subscription') and obj.subscription:
        if obj.subscription.sport_association:
            return obj.subscription.sport_association, 'subscription.sport_association'
    return None


def subscription_related_resolver(obj):
    """For models with subscription FK: SubscriptionToken, SubscriptionFile, etc."""
    if hasattr(obj, 'subscription') and obj.subscription:
        if obj.subscription.sport_association:
            return obj.subscription.sport_association, 'subscription.sport_association'
    return None


def signature_resolver(obj):
    """Signature -> subscription -> sport_association"""
    if hasattr(obj, 'subscription') and obj.subscription:
        if obj.subscription.sport_association:
            return obj.subscription.sport_association, 'subscription.sport_association'
    return None


def camps_period_resolver(obj):
    """CampsAndRetreatsPeriod -> CampsAndRetreats -> sport_association"""
    if hasattr(obj, 'camps_and_retreats') and obj.camps_and_retreats:
        if obj.camps_and_retreats.sport_association:
            return obj.camps_and_retreats.sport_association, 'camps_and_retreats.sport_association'
    return None


def camps_service_resolver(obj):
    """CampsAndRetreatsPeriodsService -> CampsAndRetreatsPeriod -> CampsAndRetreats -> sport_association"""
    if hasattr(obj, 'camps_and_retreats_period') and obj.camps_and_retreats_period:
        period = obj.camps_and_retreats_period
        if hasattr(period, 'camps_and_retreats') and period.camps_and_retreats:
            if period.camps_and_retreats.sport_association:
                return period.camps_and_retreats.sport_association, 'period.camps_and_retreats.sport_association'
    return None


def camps_subscription_resolver(obj):
    """CampsAndRetreatsSubscription -> CampsAndRetreats -> sport_association"""
    if hasattr(obj, 'camps_and_retreats') and obj.camps_and_retreats:
        if obj.camps_and_retreats.sport_association:
            return obj.camps_and_retreats.sport_association, 'camps_and_retreats.sport_association'
    return None


def camps_subscription_period_resolver(obj):
    """CampsAndRetreatsSubscriptionPeriod -> subscription -> camps -> sport_association"""
    if hasattr(obj, 'camps_and_retreats_subscription') and obj.camps_and_retreats_subscription:
        sub = obj.camps_and_retreats_subscription
        if hasattr(sub, 'camps_and_retreats') and sub.camps_and_retreats:
            if sub.camps_and_retreats.sport_association:
                return sub.camps_and_retreats.sport_association, 'subscription.camps_and_retreats.sport_association'
    return None


def invoice_rows_resolver(obj):
    """InvoiceRows -> Invoice -> sport_association"""
    if hasattr(obj, 'invoice') and obj.invoice:
        if obj.invoice.sport_association:
            return obj.invoice.sport_association, 'invoice.sport_association'
    return None


def attendance_registry_resolver(obj):
    """AttendanceRegistry -> Course -> sport_association"""
    if hasattr(obj, 'course') and obj.course:
        if obj.course.sport_association:
            return obj.course.sport_association, 'course.sport_association'
    return None


def attendance_day_resolver(obj):
    """AttendanceDay -> AttendanceRegistry -> Course -> sport_association"""
    if hasattr(obj, 'attendance_registry') and obj.attendance_registry:
        if hasattr(obj.attendance_registry, 'course') and obj.attendance_registry.course:
            if obj.attendance_registry.course.sport_association:
                return obj.attendance_registry.course.sport_association, 'attendance_registry.course.sport_association'
    return None


def module_responses_resolver(obj):
    """ModuleResponses -> Module -> sport_association"""
    if hasattr(obj, 'module') and obj.module:
        if obj.module.sport_association:
            return obj.module.sport_association, 'module.sport_association'
    return None


def carnet_subscription_resolver(obj):
    """CarnetSubscription -> Carnet -> sport_association OR Subscription -> sport_association"""
    # Try via carnet
    if hasattr(obj, 'carnet') and obj.carnet:
        if obj.carnet.sport_association:
            return obj.carnet.sport_association, 'carnet.sport_association'
    # Try via subscription
    if hasattr(obj, 'subscription') and obj.subscription:
        if obj.subscription.sport_association:
            return obj.subscription.sport_association, 'subscription.sport_association'
    return None


def user_resolver(obj):
    """User -> sport_association (via property)"""
    try:
        from application.models.user_models import SportAssociation
        # Try to get sport_association for this user
        sa = SportAssociation.objects.filter(user=obj).first()
        if sa:
            return sa, 'user.sport_association'
    except Exception:
        pass
    return None


def associate_tutor_relation_resolver(obj):
    """AssociateTutorRelation -> associate -> sport_association"""
    if hasattr(obj, 'associate') and obj.associate:
        if obj.associate.sport_association:
            return obj.associate.sport_association, 'associate.sport_association'
    return None


# =============================================================================
# SETUP FUNCTION
# =============================================================================

def setup_audit_resolvers():
    """
    Register all sport_association resolvers for audited models.
    This function should be called from apps.py ready() method.
    """
    # Import models here to avoid circular imports
    from application.models.user_models import (
        SportAssociation, Associate, Instructor, Group,
        PreviewAndCustomFeatures, UsersOnboarding, CollaborationInvites,
        InstructorHours, UserPartial, Family, AssociateTutorRelation,
        Institution, SportAssociationMembershipCardConfiguration,
        SportAssociationHeadquarter, SportAssociationModuleTemplates,
        SportAssociationMaterial, SportAssociationDocumentsArchive,
        Reminder, SentEmails, User
    )
    from application.models.payment_models import (
        Payment, PaymentCategory, SupplierAndCustomers, VatManagement
    )
    from application.models.subscriptions_models import (
        Subscription, MedicalCertificate, MedicalAppointments,
        Signature, AssociateImportDraft, SubscriptionTransfer,
        SubscriptionMembership, NotificationTemplates, SoldProducts,
        Material, SubscriptionToken, SubscriptionFile, Tags,
        AssociateTrial, AssociateImportDraftStatus, CumulativeSubscriptionGymLinks
    )
    from application.models.courses_models import (
        Course, CourseSubscription, CourseSubscriptionInstallment,
        CourseTags, CourseLocation, CampsAndRetreats, CampsAndRetreatsPeriod,
        CampsAndRetreatsPeriodsService, CampsAndRetreatsSubscriptionPeriod,
        CampsAndRetreatsSubscription
    )
    from application.models.invoices_models import (
        Invoice, InvoiceSuppliers, InvoiceRows, CustomerInvoice
    )
    from application.models.balance_sheet_models import (
        BalanceSheet, CustomAccounts, CustomAccountsTransfer
    )
    from application.models.modules_models import Module, ModuleResponses
    from application.models.attendee_models import (
        AttendanceRegistry, AttendanceDay, GlobalCalendarEvents, Reminders
    )
    from application.models.carnet_models import Carnet, CarnetSubscription
    from application.models.billing_models import (
        NurturingEmailsPlan, NurturingEmails, BillingPlan,
        BillingSubscription, BillingPayment
    )
    from communications.models import (
        Message, AutomationWorkflow, CommunicationConfiguration, SmsCreditPayment
    )

    # =========================================================================
    # SPECIAL CASE: SportAssociation is itself
    # =========================================================================
    register_resolver(SportAssociation, sport_association_self_resolver)

    # =========================================================================
    # DIRECT FK TO SPORT_ASSOCIATION
    # Models with a direct sport_association field
    # =========================================================================
    direct_fk_models = [
        # User models
        Associate,
        Instructor,
        Group,
        PreviewAndCustomFeatures,
        UsersOnboarding,
        CollaborationInvites,
        UserPartial,
        Family,
        Institution,
        SportAssociationMembershipCardConfiguration,
        SportAssociationHeadquarter,
        SportAssociationModuleTemplates,
        SportAssociationMaterial,
        SportAssociationDocumentsArchive,
        Reminder,
        SentEmails,
        # Payment models
        Payment,
        PaymentCategory,
        SupplierAndCustomers,
        # Subscription models
        Subscription,
        SubscriptionMembership,
        NotificationTemplates,
        Material,
        Tags,
        AssociateTrial,
        AssociateImportDraft,
        AssociateImportDraftStatus,
        CumulativeSubscriptionGymLinks,
        # Course models
        Course,
        CourseTags,
        CourseLocation,
        CampsAndRetreats,
        # Invoice models
        Invoice,
        InvoiceSuppliers,
        CustomerInvoice,
        # Balance sheet models
        BalanceSheet,
        CustomAccounts,
        CustomAccountsTransfer,
        # Module models
        Module,
        # Attendee models
        GlobalCalendarEvents,
        Reminders,
        # Carnet models
        Carnet,
        # Billing models
        NurturingEmailsPlan,
        NurturingEmails,
        BillingPlan,
        BillingSubscription,
        BillingPayment,
        # Communication models
        Message,
        AutomationWorkflow,
        CommunicationConfiguration,
        SmsCreditPayment,
    ]

    for model in direct_fk_models:
        register_resolver(model, direct_resolver)

    # =========================================================================
    # INDIRECT FK MODELS
    # Models that need to traverse relationships
    # =========================================================================

    # User model - special handling
    register_resolver(User, user_resolver)

    # Instructor hours
    register_resolver(InstructorHours, instructor_hours_resolver)

    # Associate tutor relation
    register_resolver(AssociateTutorRelation, associate_tutor_relation_resolver)

    # Course subscriptions
    register_resolver(CourseSubscription, course_subscription_resolver)
    register_resolver(CourseSubscriptionInstallment, course_subscription_installment_resolver)

    # Medical
    register_resolver(MedicalCertificate, medical_certificate_resolver)
    register_resolver(MedicalAppointments, medical_appointments_resolver)

    # Subscription-related
    register_resolver(SubscriptionToken, subscription_related_resolver)
    register_resolver(SubscriptionFile, subscription_related_resolver)
    register_resolver(SubscriptionTransfer, subscription_related_resolver)
    register_resolver(Signature, signature_resolver)
    register_resolver(SoldProducts, subscription_related_resolver)

    # Camps and retreats
    register_resolver(CampsAndRetreatsPeriod, camps_period_resolver)
    register_resolver(CampsAndRetreatsPeriodsService, camps_service_resolver)
    register_resolver(CampsAndRetreatsSubscription, camps_subscription_resolver)
    register_resolver(CampsAndRetreatsSubscriptionPeriod, camps_subscription_period_resolver)

    # Invoice rows
    register_resolver(InvoiceRows, invoice_rows_resolver)

    # Attendance
    register_resolver(AttendanceRegistry, attendance_registry_resolver)
    register_resolver(AttendanceDay, attendance_day_resolver)

    # Module responses
    register_resolver(ModuleResponses, module_responses_resolver)

    # Carnet subscription
    register_resolver(CarnetSubscription, carnet_subscription_resolver)

    # VatManagement has no sport_association - will fallback to actor
    register_resolver(VatManagement, lambda obj: None)

    logger.info(f"Registered {len(SPORT_ASSOCIATION_RESOLVERS)} audit log resolvers")
