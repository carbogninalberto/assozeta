"""
Test fixture factories for the assozeta self-hosted application.

All factories produce deterministic-but-unique test instances with sensible
defaults compatible with the current self-host models. They accept keyword
arguments to override any field.

Factories are plain functions (not pytest fixtures) so they work inside
both Django ``TestCase`` subclasses and pytest tests without any plugin.
"""
import datetime
import uuid
from decimal import Decimal

from django.utils import timezone
from django.contrib.auth import get_user_model

from instance.models import InstanceConfiguration
from docmanager.models import Document

from application.models import (
    BillingPlan,
    BillingSubscription,
    User,
    SportAssociation,
    Associate,
    AssociateTutorRelation,
    Family,
    Subscription,
    MedicalCertificate,
    MedicalAppointments,
    Tags,
    SubscriptionToken,
    AssociateImportDraft,
    Payment,
    PaymentCategory,
    SupplierAndCustomers,
    Course,
    CourseLocation,
    CourseSubscription,
    CourseSubscriptionInstallment,
    Invoice,
    InvoiceSuppliers,
    CustomerInvoice,
)
from application.models.balance_sheet_models import CustomAccounts


_default_email_counter = [0]


def _next_email():
    _default_email_counter[0] += 1
    return f"test+{_default_email_counter[0]}@selfhosted.test"


def _next_tax_code():
    return f"TC{uuid.uuid4().hex[:9].upper()}"


def _next_username():
    return f"testuser_{uuid.uuid4().hex[:8]}"


# ── User ────────────────────────────────────────────────────────────────

def create_test_user(**kwargs):
    """
    Create a test ``User`` with deterministic-but-unique defaults.

    Default role is ``User.ASSOCIATION``.
    """
    password = kwargs.pop('password', 'testpass123')
    defaults = {
        'username': _next_username(),
        'email': _next_email(),
        'first_name': 'Testfirst',
        'last_name': 'Testlast',
        'role': User.ASSOCIATION,
        'subscription_duration': User.LIKE_SEASON_YEAR,
        'subscription_start_day': 1,
        'subscription_start_month': 9,
        'balance_sheet_start_day': 1,
        'balance_sheet_start_month': 1,
        'online_payments': True,
    }
    defaults.update(kwargs)
    password = defaults.pop('password', password)

    user = User.objects.create_user(**defaults)
    user.set_password(password)
    user.save()
    return user


def create_test_sport_association(user=None, **kwargs):
    """
    Create a test ``SportAssociation``.

    If *user* is omitted a new association-role user is created.
    """
    if user is None:
        user = create_test_user(role=User.ASSOCIATION)

    defaults = {
        'denomination': f"Test Association {uuid.uuid4().hex[:6]}",
        'tax_code': _next_tax_code(),
        'subscription_fee': Decimal('100.00'),
        'user': user,
    }
    defaults.update(kwargs)
    return SportAssociation.objects.create(**defaults)


# ── InstanceConfiguration (singleton helper) ────────────────────────────

def create_test_instance_config(primary_association=None, **kwargs):
    """
    Create (or return existing) ``InstanceConfiguration`` singleton.

    By default it is marked *self_hosted* with ``SETUP_PROVENANCE_FRESH``.
    """
    existing = InstanceConfiguration.objects.first()
    if existing is not None:
        return existing

    if primary_association is None:
        primary_association = create_test_sport_association()

    defaults = {
        'domain': 'selfhost.test',
        'name': 'Self-Hosted Test Instance',
        'setup_provenance': InstanceConfiguration.SETUP_PROVENANCE_FRESH,
        'self_hosted': True,
        'primary_association': primary_association,
    }
    defaults.update(kwargs)
    return InstanceConfiguration.objects.create(**defaults)


# ── Billing ─────────────────────────────────────────────────────────────

def create_test_billing_subscription(user=None, plan_type='pro', **kwargs):
    """
    Create a test ``BillingSubscription`` for a user.

    *plan_type* is one of ``'base'``, ``'pro'``, ``'teams'`` (default ``'pro'``
    to reflect the self-host included Pro entitlement).
    """
    if user is None:
        user = create_test_user(role=User.ASSOCIATION)

    plan_map = {
        'base': BillingPlan.BASE_PLAN,
        'pro': BillingPlan.PRO_PLAN,
        'teams': BillingPlan.TEAMS_PLAN,
    }
    billing_type = plan_map.get(plan_type, BillingPlan.PRO_PLAN)

    plan_names = {
        BillingPlan.BASE_PLAN: "Piano Base",
        BillingPlan.PRO_PLAN: "Piano Pro",
        BillingPlan.TEAMS_PLAN: "Piano Teams",
    }
    plan, _ = BillingPlan.objects.get_or_create(
        name=plan_names.get(billing_type, "Piano Pro"),
        defaults={
            'description': f'{plan_type.title()} plan for testing',
            'monthly_fee': 0 if billing_type == BillingPlan.BASE_PLAN else 10,
            'annually_fee': 0 if billing_type == BillingPlan.BASE_PLAN else 100,
            'billing_type': billing_type,
        },
    )

    defaults = {
        'user': user,
        'auto_renewal': True,
        'renewal_type': BillingSubscription.ANNUALLY,
        'ends_on': timezone.now() + datetime.timedelta(days=365),
        'billing_plan': plan,
    }
    defaults.update(kwargs)
    return BillingSubscription.objects.create(**defaults)


# ── Family ──────────────────────────────────────────────────────────────

def create_test_family(**kwargs):
    defaults = {
        'type': Family.FAMILY,
    }
    defaults.update(kwargs)
    return Family.objects.create(**defaults)


# ── Associate ───────────────────────────────────────────────────────────

def create_test_associate(sport_association=None, user=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'first_name': f"AssocFirst{uuid.uuid4().hex[:6]}",
        'last_name': f"AssocLast{uuid.uuid4().hex[:6]}",
        'sex': Associate.MALE,
        'tax_code': f"{uuid.uuid4().hex[:16].upper()}",
        'born_date': datetime.date(1990, 1, 1),
        'born_city': 'TestCity',
        'address_city': 'TestCity',
        'address': 'Via Test 1',
        'address_cap': '00100',
        'phone': '+39123456789',
        'email': _next_email(),
        'user': user,
        'sport_association': sport_association,
    }
    defaults.update(kwargs)
    return Associate.objects.create(**defaults)


def create_test_minor_associate(sport_association=None, with_tutor=True, **kwargs):
    today = datetime.date.today()
    minor_birth_date = datetime.date(today.year - 10, today.month, today.day)

    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'born_date': minor_birth_date,
        'first_name': f"Minor{uuid.uuid4().hex[:6]}",
        'last_name': f"Minorson{uuid.uuid4().hex[:6]}",
        'is_minor': True,
    }
    defaults.update(kwargs)
    minor = create_test_associate(sport_association=sport_association, **defaults)

    tutor_relation = None
    if with_tutor:
        tutor = create_test_associate(
            sport_association=sport_association,
            first_name=f"Tutor{uuid.uuid4().hex[:6]}",
            born_date=datetime.date(1980, 1, 1),
        )
        tutor_relation = AssociateTutorRelation.objects.create(
            associate=minor,
            tutor=tutor,
            is_primary=True,
        )

    return minor, tutor_relation


# ── Subscription ────────────────────────────────────────────────────────

def create_test_subscription(sport_association=None, associate=None, user=None,
                             with_payment=False, with_medical=False, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()
    if associate is None:
        associate = create_test_associate(sport_association=sport_association)
    if user is None:
        user = associate.user if associate.user else sport_association.user

    defaults = {
        'sport_association': sport_association,
        'associate': associate,
        'user': user,
        'status_flag': Subscription.ACCEPTED,
        'type': Subscription.ASSOCIATE_AND_MEMBER,
        'role': Subscription.SOCIO_ORDINARIO,
        'draft': False,
        'archived': False,
        'deleted': False,
    }
    defaults.update(kwargs)

    if with_payment and 'payment' not in defaults:
        payment = create_test_payment(
            user=user,
            associate=associate,
            sport_association=sport_association,
        )
        defaults['payment'] = payment

    if with_medical and 'medical' not in defaults:
        medical = create_test_medical_certificate(user=user)
        defaults['medical'] = medical

    return Subscription.objects.create(**defaults)


def create_test_archived_subscription(**kwargs):
    kwargs.setdefault('archived', True)
    return create_test_subscription(**kwargs)


def create_bulk_subscriptions(count=10, sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    subscriptions = []
    for index in range(count):
        associate = create_test_associate(
            sport_association=sport_association,
            first_name=f'Bulk{index}',
            last_name=f'Associate{index}',
        )
        subscriptions.append(create_test_subscription(
            sport_association=sport_association,
            associate=associate,
            **kwargs,
        ))
    return subscriptions


# ── Medical ─────────────────────────────────────────────────────────────

def create_test_medical_certificate(user=None, **kwargs):
    if user is None:
        user = create_test_user()

    defaults = {
        'user': user,
        'expiration_date': timezone.now().date() + datetime.timedelta(days=365),
        'competitive_medical_certificate': False,
    }
    defaults.update(kwargs)
    return MedicalCertificate.objects.create(**defaults)


def create_test_medical_appointment(subscription=None, **kwargs):
    if subscription is None:
        subscription = create_test_subscription()

    defaults = {
        'subscription': subscription,
        'region': 'agonistica',
        'date': timezone.now().date() + datetime.timedelta(days=7),
        'sport': 'Calcio',
    }
    defaults.update(kwargs)
    return MedicalAppointments.objects.create(**defaults)


# ── Tags ────────────────────────────────────────────────────────────────

def create_test_tag(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'tag_name': f"Tag-{uuid.uuid4().hex[:6]}",
        'sport_association': sport_association,
    }
    defaults.update(kwargs)
    return Tags.objects.create(**defaults)


def create_test_subscription_token(subscription=None, **kwargs):
    if subscription is None:
        subscription = create_test_subscription()

    defaults = {
        'subscription': subscription,
        'token': uuid.uuid4(),
        'expiration_date': timezone.now() + datetime.timedelta(days=7),
    }
    defaults.update(kwargs)
    return SubscriptionToken.objects.create(**defaults)


def create_test_import_draft(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'sport_association': sport_association,
        'data': {
            'first_name': f"Draft{uuid.uuid4().hex[:4]}",
            'last_name': f"Assoc{uuid.uuid4().hex[:4]}",
            'tax_code': f"{uuid.uuid4().hex[:16].upper()}",
            'born_date': '1990-01-01',
        },
        'valid': False,
    }
    defaults.update(kwargs)
    return AssociateImportDraft.objects.create(**defaults)


# ── Course / Location ───────────────────────────────────────────────────

def create_test_course(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'title': f"Test Course {uuid.uuid4().hex[:6]}",
        'description': 'Course created by test factory',
        'fee': Decimal('50.00'),
        'status_flag': Course.ACTIVE,
        'sport_association': sport_association,
    }
    defaults.update(kwargs)
    return Course.objects.create(**defaults)


def create_test_course_location(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'title': f"Location {uuid.uuid4().hex[:6]}",
        'address': 'Via Palestra 42',
        'sport_association': sport_association,
    }
    defaults.update(kwargs)
    return CourseLocation.objects.create(**defaults)


def create_test_course_subscription(course=None, subscription=None, **kwargs):
    if course is None:
        course = create_test_course()
    if subscription is None:
        subscription = create_test_subscription(sport_association=course.sport_association)

    defaults = {
        'course': course,
        'subscription': subscription,
        'paid': False,
    }
    defaults.update(kwargs)
    return CourseSubscription.objects.create(**defaults)


def create_test_course_subscription_installment(course_subscription=None, **kwargs):
    if course_subscription is None:
        course_subscription = create_test_course_subscription()

    defaults = {
        'course_subscription': course_subscription,
        'id': 1,
        'amount': course_subscription.course.fee,
        'paid': False,
        'payment_date': timezone.now() + datetime.timedelta(days=30),
    }
    defaults.update(kwargs)
    return CourseSubscriptionInstallment.objects.create(**defaults)


# ── Payment / Category / CustomAccount / Supplier ───────────────────────

def create_test_payment_category(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'name': 'entrate e proventi da attivita tipiche',
        'sport_association': sport_association,
    }
    defaults.update(kwargs)
    return PaymentCategory.objects.create(**defaults)


def create_test_custom_account(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'name': f"Bank account {uuid.uuid4().hex[:4]}",
        'initial_balance': Decimal('0.00'),
        'account_type': CustomAccounts.BANK,
        'account_code': f"ACCT-{uuid.uuid4().hex[:4].upper()}",
        'sport_association': sport_association,
        'editable': False,
        'enabled': True,
    }
    defaults.update(kwargs)
    return CustomAccounts.objects.create(**defaults)


def create_test_supplier(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'name': f"Supplier {uuid.uuid4().hex[:6]}",
        'tax_code': _next_tax_code(),
        'sport_association': sport_association,
        'type': 'supplier',
    }
    defaults.update(kwargs)
    return SupplierAndCustomers.objects.create(**defaults)


def create_test_payment(user=None, associate=None, sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()
    if user is None:
        user = sport_association.user
    if associate is None:
        associate = create_test_associate(sport_association=sport_association)

    defaults = {
        'user': user,
        'associate': associate,
        'amount': Decimal('100.00'),
        'subject': Payment.SUBSCRIPTION,
        'sport_association': sport_association,
        'payment_date': timezone.now(),
    }
    defaults.update(kwargs)
    return Payment.objects.create(**defaults)


# ── Invoice ─────────────────────────────────────────────────────────────

def create_test_invoice(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'sport_association': sport_association,
        'membership_fee': Decimal('50.00'),
        'activity_fee': Decimal('50.00'),
        'number': 1,
    }
    defaults.update(kwargs)
    return Invoice.objects.create(**defaults)


def create_test_invoice_supplier(sport_association=None, supplier=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()
    if supplier is None:
        supplier = create_test_supplier(sport_association=sport_association)

    defaults = {
        'invoice_identifier': f"INV-{uuid.uuid4().hex[:8].upper()}",
        'amount': Decimal('200.00'),
        'sport_association': sport_association,
        'supplier': supplier,
    }
    defaults.update(kwargs)
    return InvoiceSuppliers.objects.create(**defaults)


def create_test_customer_invoice(sport_association=None, **kwargs):
    if sport_association is None:
        sport_association = create_test_sport_association()

    defaults = {
        'sport_association': sport_association,
        'prefix': 'FPR',
        'number': 1,
        'fiscal_year': timezone.now().year,
        'transferor_denomination': 'Test Transferor',
        'payment_total_amount': Decimal('0.00'),
    }
    defaults.update(kwargs)
    return CustomerInvoice.objects.create(**defaults)


# ── Document ────────────────────────────────────────────────────────────

def create_test_document(**kwargs):
    defaults = {
        'filename': f"doc_{uuid.uuid4().hex[:8]}.pdf",
        'filepath': f"test/docs/doc_{uuid.uuid4().hex[:8]}.pdf",
    }
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


# ── Self-host scenario (integration helper) ─────────────────────────────

def create_self_host_scenario(pro_entitlement=True, with_instance_config=True):
    """
    One-stop helper that creates a full self-host test world.

    Returns a dict with keys: *user*, *sport_association*, *instance_config*,
    *billing_subscription*, *associate*, *payment*, *subscription*.
    """
    user = create_test_user(role=User.ASSOCIATION)
    sport_association = create_test_sport_association(user=user)

    instance_config = None
    if with_instance_config:
        instance_config = create_test_instance_config(
            primary_association=sport_association,
        )

    billing_subscription = None
    if pro_entitlement:
        billing_subscription = create_test_billing_subscription(
            user=user, plan_type='pro',
        )

    associate = create_test_associate(sport_association=sport_association)

    payment = create_test_payment(
        user=user,
        associate=associate,
        sport_association=sport_association,
    )

    subscription = create_test_subscription(
        sport_association=sport_association,
        associate=associate,
        user=user,
        with_payment=True,
        status_flag=Subscription.ACCEPTED,
    )

    return {
        'user': user,
        'sport_association': sport_association,
        'instance_config': instance_config,
        'billing_subscription': billing_subscription,
        'associate': associate,
        'payment': payment,
        'subscription': subscription,
    }
