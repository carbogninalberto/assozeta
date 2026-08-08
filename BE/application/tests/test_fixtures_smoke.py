"""
Smoke test for the shared fixture factory layer.

Verifies that every factory creates a valid instance and that the base
test classes (with auditlog disabled) work correctly.
"""
from decimal import Decimal

from application.tests.base import BaseTestCase, BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_user,
    create_test_sport_association,
    create_test_instance_config,
    create_test_billing_subscription,
    create_test_associate,
    create_test_family,
    create_test_minor_associate,
    create_test_subscription,
    create_test_course,
    create_test_course_location,
    create_test_course_subscription,
    create_test_course_subscription_installment,
    create_test_payment,
    create_test_payment_category,
    create_test_custom_account,
    create_test_invoice,
    create_test_invoice_supplier,
    create_test_customer_invoice,
    create_test_supplier,
    create_test_medical_certificate,
    create_test_medical_appointment,
    create_test_document,
    create_test_tag,
    create_test_subscription_token,
    create_test_import_draft,
    create_self_host_scenario,
)
from application.models import (
    User,
    SportAssociation,
    BillingPlan,
    BillingSubscription,
    Family,
    Associate,
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
from instance.models import InstanceConfiguration


class FactorySmokeTests(BaseTestCase):
    """Test every factory creates a valid model instance."""

    def test_create_test_user(self):
        u = create_test_user()
        self.assertIsInstance(u, User)
        self.assertTrue(u.check_password('testpass123'))

    def test_user_roles(self):
        for role, _label in [(User.ASSOCIATION, 'assoc'),
                             (User.ATHLETE, 'athl'),
                             (User.COLLABORATOR, 'collab')]:
            u = create_test_user(role=role)
            self.assertEqual(u.role, role)

    def test_create_sport_association(self):
        sa = create_test_sport_association()
        self.assertIsInstance(sa, SportAssociation)
        self.assertEqual(sa.user.role, User.ASSOCIATION)

    def test_create_instance_config(self):
        sa = create_test_sport_association()
        ic = create_test_instance_config(primary_association=sa)
        self.assertIsInstance(ic, InstanceConfiguration)
        self.assertTrue(ic.self_hosted)
        self.assertEqual(ic.primary_association, sa)
        # singleton: second call returns same instance
        ic2 = create_test_instance_config()
        self.assertEqual(ic.pk, ic2.pk)

    def test_create_billing_subscription_pro_is_default(self):
        bs = create_test_billing_subscription()
        self.assertIsInstance(bs, BillingSubscription)
        self.assertEqual(bs.billing_plan.billing_type, BillingPlan.PRO_PLAN)
        self.assertTrue(bs.is_active())

    def test_create_billing_subscription_base(self):
        bs = create_test_billing_subscription(plan_type='base')
        self.assertEqual(bs.billing_plan.billing_type, BillingPlan.BASE_PLAN)

    def test_create_family(self):
        f = create_test_family()
        self.assertIsInstance(f, Family)
        self.assertEqual(f.type, Family.FAMILY)

    def test_create_associate(self):
        a = create_test_associate()
        self.assertIsInstance(a, Associate)
        self.assertIsNotNone(a.sport_association)

    def test_create_minor_associate(self):
        minor, tutor_rel = create_test_minor_associate()
        self.assertIsInstance(minor, Associate)
        self.assertTrue(minor.is_minor_now)
        self.assertIsNotNone(tutor_rel)
        self.assertTrue(tutor_rel.is_primary)

    def test_create_subscription(self):
        sub = create_test_subscription()
        self.assertIsInstance(sub, Subscription)
        self.assertEqual(sub.status_flag, Subscription.ACCEPTED)

    def test_create_subscription_with_payment(self):
        sub = create_test_subscription(with_payment=True)
        self.assertIsNotNone(sub.payment)

    def test_create_medical_certificate(self):
        mc = create_test_medical_certificate()
        self.assertIsInstance(mc, MedicalCertificate)

    def test_create_medical_appointment(self):
        ma = create_test_medical_appointment()
        self.assertIsInstance(ma, MedicalAppointments)

    def test_create_tag(self):
        t = create_test_tag()
        self.assertIsInstance(t, Tags)

    def test_create_subscription_token(self):
        st = create_test_subscription_token()
        self.assertIsInstance(st, SubscriptionToken)
        self.assertIsNotNone(st.subscription)

    def test_create_import_draft(self):
        d = create_test_import_draft()
        self.assertIsInstance(d, AssociateImportDraft)
        self.assertIsNotNone(d.data)
        self.assertIn('first_name', d.data)

    def test_create_course(self):
        c = create_test_course()
        self.assertIsInstance(c, Course)

    def test_create_course_location(self):
        loc = create_test_course_location()
        self.assertIsInstance(loc, CourseLocation)

    def test_create_course_subscription(self):
        cs = create_test_course_subscription()
        self.assertIsInstance(cs, CourseSubscription)

    def test_create_course_subscription_installment(self):
        csi = create_test_course_subscription_installment()
        self.assertIsInstance(csi, CourseSubscriptionInstallment)
        self.assertFalse(csi.paid)

    def test_create_payment_category(self):
        pc = create_test_payment_category()
        self.assertIsInstance(pc, PaymentCategory)

    def test_create_custom_account(self):
        ca = create_test_custom_account()
        self.assertIsInstance(ca, CustomAccounts)
        self.assertEqual(ca.account_type, CustomAccounts.BANK)

    def test_create_supplier(self):
        s = create_test_supplier()
        self.assertIsInstance(s, SupplierAndCustomers)

    def test_create_payment(self):
        p = create_test_payment()
        self.assertIsInstance(p, Payment)
        self.assertEqual(p.amount, Decimal('100.00'))

    def test_create_invoice(self):
        inv = create_test_invoice()
        self.assertIsInstance(inv, Invoice)
        self.assertEqual(inv.total_amount, Decimal('100.00'))

    def test_create_invoice_supplier(self):
        invs = create_test_invoice_supplier()
        self.assertIsInstance(invs, InvoiceSuppliers)

    def test_create_customer_invoice(self):
        ci = create_test_customer_invoice()
        self.assertIsInstance(ci, CustomerInvoice)

    def test_create_document(self):
        d = create_test_document()
        self.assertEqual(d.filename.endswith('.pdf'), True)

    def test_self_host_scenario(self):
        scenario = create_self_host_scenario()
        for key in ['user', 'sport_association', 'instance_config',
                     'billing_subscription', 'associate', 'payment', 'subscription']:
            self.assertIsNotNone(scenario.get(key), f"Missing key: {key}")
        self.assertTrue(scenario['instance_config'].self_hosted)


class BaseAPITestCaseSmokeTests(BaseAPITestCase):
    """Verify the BaseAPITestCase sets up everything correctly."""

    def test_client_is_authenticated(self):
        self.assertIsNotNone(self.client)

    def test_user_is_association(self):
        self.assertEqual(self.user.role, User.ASSOCIATION)

    def test_sport_association_linked(self):
        self.assertEqual(self.sport_association.user, self.user)

    def test_billing_subscription_is_active(self):
        self.assertTrue(self.billing_subscription.is_active())

    def test_instance_config_exists(self):
        self.assertIsNotNone(self.instance_config)
        self.assertTrue(self.instance_config.self_hosted)

    def test_create_authenticated_client_helper(self):
        client = self.create_authenticated_client()
        self.assertIsNotNone(client)
