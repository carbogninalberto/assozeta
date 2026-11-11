"""The primary key in a request body is never written.

Update endpoints save the row the path names in place (no row is inserted under the
body's key, no other row is overwritten) and answer with the path row's key. Create
endpoints generate the key themselves.
"""
import datetime
import uuid
from decimal import Decimal
from unittest import mock

from django.utils import timezone

from application.models import (
    CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService, CourseLocation, CourseSubscription,
    CourseSubscriptionInstallment, Instructor, InstructorHours, InvoiceSuppliers, MedicalAppointments, Module, Payment,
    SportAssociationDocumentsArchive, SportAssociationModuleTemplates, Subscription, SubscriptionMembership,
    SupplierAndCustomers,
)
from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_course, create_test_course_location, create_test_course_subscription,
    create_test_course_subscription_installment, create_test_custom_account, create_test_document, create_test_payment,
    create_test_subscription, create_test_supplier,
)


class UpdateKeepsPrimaryKeyTests(BaseAPITestCase):

    def assert_in_place(self, model, row, response, **stored):
        self.assertEqual(response.status_code, 200, getattr(response, 'data', response.content))
        self.assertEqual(model._base_manager.filter(pk=self.new_key).count(), 0)
        row.refresh_from_db()
        for name, value in stored.items():
            self.assertEqual(getattr(row, name), value)

    def setUp(self):
        super().setUp()
        self.new_key = uuid.uuid4()

    def test_course_location_update(self):
        location = create_test_course_location(sport_association=self.sport_association, title='Palestra')
        other = create_test_course_location(sport_association=self.sport_association, title='Piscina')
        response = self.client.patch(f'/course/locations/{location.pk}/update',
                                     {'course_location_id': str(self.new_key), 'title': 'Palestra nuova'}, format='json')
        self.assert_in_place(CourseLocation, location, response, title='Palestra nuova')
        self.assertEqual(response.data['course_location_id'], str(location.pk))
        response = self.client.put(f'/course-locations/{location.pk}/',
                                   {'course_location_id': str(other.pk), 'title': 'Palestra grande'}, format='json')
        self.assert_in_place(CourseLocation, location, response, title='Palestra grande')
        other.refresh_from_db()
        self.assertEqual(other.title, 'Piscina')

    def test_course_subscription_update(self):
        course = create_test_course(sport_association=self.sport_association)
        row = create_test_course_subscription(course=course)
        deleted = create_test_course_subscription(course=course, deleted=True)
        response = self.client.patch(f'/course-subscriptions/{row.pk}/update',
                                     {'course_subscription_id': str(self.new_key), 'paid': True}, format='json')
        self.assert_in_place(CourseSubscription, row, response, paid=True)
        self.assertEqual(response.data['course_subscription_id'], str(row.pk))
        response = self.client.patch(f'/course-subscriptions/{row.pk}/update',
                                     {'course_subscription_id': str(deleted.pk), 'paid': False}, format='json')
        self.assert_in_place(CourseSubscription, row, response, paid=False, deleted=False)
        deleted = CourseSubscription._base_manager.get(pk=deleted.pk)
        self.assertTrue(deleted.deleted)
        self.assertFalse(deleted.paid)

    def test_course_subscription_add_generates_key(self):
        course = create_test_course(sport_association=self.sport_association)
        subscription = create_test_subscription(sport_association=self.sport_association)
        response = self.client.post('/course-subscriptions/add', [{
            'course_subscription_id': str(self.new_key), 'course': str(course.pk),
            'subscription_id': str(subscription.pk),
        }], format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(response.data[0]['course_subscription_id'], str(self.new_key))
        self.assertFalse(CourseSubscription._base_manager.filter(pk=self.new_key).exists())

    def test_supplier_update(self):
        supplier = create_test_supplier(sport_association=self.sport_association, name='Fornitore')
        deleted = create_test_supplier(sport_association=self.sport_association, name='Vecchio', deleted=True)
        response = self.client.patch(f'/supplier/{supplier.pk}/update',
                                     {'supplier_id': str(self.new_key), 'name': 'Fornitore nuovo'}, format='json')
        self.assert_in_place(SupplierAndCustomers, supplier, response, name='Fornitore nuovo')
        self.assertEqual(response.data['supplier_id'], str(supplier.pk))
        response = self.client.patch(f'/supplier/{supplier.pk}/update',
                                     {'supplier_id': str(deleted.pk), 'name': 'Fornitore'}, format='json')
        self.assert_in_place(SupplierAndCustomers, supplier, response, name='Fornitore')
        deleted = SupplierAndCustomers._base_manager.get(pk=deleted.pk)
        self.assertEqual((deleted.name, deleted.deleted), ('Vecchio', True))

    def test_supplier_add_generates_key(self):
        response = self.client.post('/supplier/add', {'supplier_id': str(self.new_key), 'name': 'Nuovo'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(response.data['supplier_id'], str(self.new_key))
        self.assertFalse(SupplierAndCustomers._base_manager.filter(pk=self.new_key).exists())

    def test_balance_account_update(self):
        account = create_test_custom_account(sport_association=self.sport_association, name='Banca')
        response = self.client.patch(f'/balance-sheet/accounts/{account.pk}/update',
                                     {'custom_account_id': str(self.new_key), 'name': 'Banca nuova'}, format='json')
        self.assert_in_place(CustomAccounts, account, response, name='Banca nuova')
        self.assertEqual(response.data['custom_account_id'], str(account.pk))

    def test_balance_transfer_update(self):
        source = create_test_custom_account(sport_association=self.sport_association)
        target = create_test_custom_account(sport_association=self.sport_association)
        transfer = CustomAccountsTransfer.objects.create(custom_account_from=source, custom_account_to=target,
                                                         amount=Decimal('10.00'), sport_association=self.sport_association)
        response = self.client.patch(f'/balance-sheet/accounts-transfer/{transfer.pk}/update',
                                     {'custom_account_transfer_id': str(self.new_key), 'amount': '12.50'}, format='json')
        self.assert_in_place(CustomAccountsTransfer, transfer, response, amount=Decimal('12.50'))
        self.assertEqual(response.data['custom_account_transfer_id'], str(transfer.pk))

    def test_instructor_and_hours_update(self):
        instructor = Instructor.objects.create(user=self.user, first_name='Ada', last_name='Rossi')
        hours = InstructorHours.objects.create(instructor=instructor, hours=Decimal('2.00'))
        response = self.client.patch(f'/instructor/{instructor.pk}/update', {
            'instructor_id': str(self.new_key), 'first_name': 'Ada', 'last_name': 'Bianchi'}, format='json')
        self.assert_in_place(Instructor, instructor, response, last_name='Bianchi')
        response = self.client.patch(f'/instructor/{instructor.pk}/hours/{hours.pk}/update',
                                     {'instructor_hours_id': str(self.new_key), 'hours': '3.00'}, format='json')
        self.assert_in_place(InstructorHours, hours, response, hours=Decimal('3.00'))

    def test_module_update(self):
        module = Module.objects.create(sport_association=self.sport_association, title='Iscrizione')
        response = self.client.patch(f'/modules/{module.pk}/update',
                                     {'module_id': str(self.new_key), 'title': 'Iscrizione 2026'}, format='json')
        self.assert_in_place(Module, module, response, title='Iscrizione 2026')
        self.assertEqual(response.data['camp_and_retreat']['module_id'], str(module.pk))

    def test_camps_updates(self):
        camp = CampsAndRetreats.objects.create(sport_association=self.sport_association, title='Campo')
        start = timezone.now()
        period = CampsAndRetreatsPeriod.objects.create(camps_and_retreats=camp, start_date=start,
                                                       end_date=start + datetime.timedelta(days=7), title='Luglio')
        service = CampsAndRetreatsPeriodsService.objects.create(camps_and_retreats_period=period, title='Pranzo')
        response = self.client.patch(f'/camps-and-retreats/{camp.pk}/update',
                                     {'camps_and_retreats_id': str(self.new_key), 'title': 'Campo estivo'}, format='json')
        self.assert_in_place(CampsAndRetreats, camp, response, title='Campo estivo')
        self.assertEqual(response.data['camp_and_retreat']['camps_and_retreats_id'], str(camp.pk))
        response = self.client.patch(f'/camps-and-retreats/periods/{period.pk}/update',
                                     {'camps_and_retreats_period_id': str(self.new_key), 'title': 'Agosto'}, format='json')
        self.assert_in_place(CampsAndRetreatsPeriod, period, response, title='Agosto')
        response = self.client.patch(f'/camps-and-retreats/periods/services/{service.pk}/update',
                                     {'camps_and_retreats_period_service_id': str(self.new_key), 'title': 'Cena'},
                                     format='json')
        self.assert_in_place(CampsAndRetreatsPeriodsService, service, response, title='Cena')

    def test_invoice_supplier_update(self):
        invoice = InvoiceSuppliers.objects.create(sport_association=self.sport_association, invoice_identifier='F-1',
                                                  amount=Decimal('50.00'))
        response = self.client.patch(f'/invoice-suppliers/{invoice.pk}/update',
                                     {'invoice_supplier_id': str(self.new_key), 'invoice_identifier': 'F-2',
                                      'paid': False}, format='json')
        self.assert_in_place(InvoiceSuppliers, invoice, response, invoice_identifier='F-2')

    def test_document_archive_and_template_update(self):
        archive = SportAssociationDocumentsArchive.objects.create(sport_association=self.sport_association,
                                                                  document=create_test_document())
        template = SportAssociationModuleTemplates.objects.create(sport_association=self.sport_association, name='Modulo')
        # The archive update saves before rendering its answer, whose nested document fails (500).
        self.client.raise_request_exception = False
        response = self.client.patch(f'/documents/{archive.pk}/update',
                                     {'sport_association_documents_archive_id': str(self.new_key), 'date': '01/02/2026'},
                                     format='json')
        self.client.raise_request_exception = True
        self.assertEqual(response.status_code, 500)
        self.assertFalse(SportAssociationDocumentsArchive._base_manager.filter(pk=self.new_key).exists())
        archive.refresh_from_db()
        self.assertEqual(archive.date, datetime.date(2026, 2, 1))
        response = self.client.patch(f'/templates/{template.pk}/update',
                                     {'sport_association_module_templates_id': str(self.new_key), 'name': 'Modulo B'},
                                     format='json')
        self.assert_in_place(SportAssociationModuleTemplates, template, response, name='Modulo B')
        self.assertEqual(response.data['sport_association_module_templates_id'], str(template.pk))

    def test_membership_update(self):
        membership = SubscriptionMembership.objects.create(sport_association=self.sport_association,
                                                           membership_type='CSEN')
        response = self.client.patch(f'/subscription/memberships/{membership.pk}/update',
                                     {'subscription_membership_id': str(self.new_key), 'membership_type': 'UISP'},
                                     format='json')
        self.assert_in_place(SubscriptionMembership, membership, response, membership_type='UISP')
        self.assertEqual(response.data['subscription_membership_id'], str(membership.pk))

    def test_persona_subscription_update(self):
        subscription = create_test_subscription(sport_association=self.sport_association, notes='prima')
        deleted = create_test_subscription(sport_association=self.sport_association, notes='vecchia', deleted=True)
        response = self.client.patch(f'/personas-subscriptions/{subscription.pk}/',
                                     {'subscription_id': str(deleted.pk), 'notes': 'dopo'}, format='json')
        self.assert_in_place(Subscription, subscription, response, notes='dopo')
        self.assertEqual(response.data['subscription_id'], str(subscription.pk))
        deleted = Subscription._base_manager.get(pk=deleted.pk)
        self.assertEqual((deleted.notes, deleted.deleted), ('vecchia', True))

    def test_medical_appointment_add_generates_key(self):
        subscription = create_test_subscription(sport_association=self.sport_association)
        with mock.patch('application.views.subscriptions_views.print_document_medical_appointment'):
            response = self.client.post(f'/subscription/{subscription.pk}/medical-appointments/add', {
                'medical_appointments_id': str(self.new_key), 'region': 'Veneto'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(MedicalAppointments.objects.filter(pk=self.new_key).exists())
        self.assertEqual(MedicalAppointments.objects.filter(subscription=subscription).count(), 1)


class CourseSubscriptionDeleteTests(BaseAPITestCase):
    """A row whose own payment is also an unpaid installment's payment."""

    def shared(self):
        course = create_test_course(sport_association=self.sport_association)
        subscription = create_test_subscription(sport_association=self.sport_association)
        payment = create_test_payment(sport_association=self.sport_association, paid=False)
        row = create_test_course_subscription(course=course, subscription=subscription, payment=payment)
        installment = create_test_course_subscription_installment(course_subscription=row, payment=payment)
        return row, installment, payment

    def assert_deleted(self, row, installment, payment):
        row = CourseSubscription._base_manager.get(pk=row.pk)
        self.assertTrue(row.deleted)
        self.assertIsNone(row.payment_id)
        self.assertFalse(CourseSubscriptionInstallment._base_manager.filter(pk=installment.pk).exists())
        self.assertFalse(Payment._base_manager.filter(pk=payment.pk).exists())

    def test_delete_route_completes(self):
        row, installment, payment = self.shared()
        response = self.client.delete(f'/course-subscriptions/{row.pk}/delete')
        self.assertEqual(response.status_code, 204)
        self.assert_deleted(row, installment, payment)

    def test_router_destroy_completes(self):
        row, installment, payment = self.shared()
        response = self.client.delete(f'/course-subscriptions/{row.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assert_deleted(row, installment, payment)

    def test_failed_save_changes_nothing(self):
        row, installment, payment = self.shared()
        self.client.raise_request_exception = False
        with mock.patch.object(CourseSubscription, 'save', side_effect=RuntimeError('save failed')):
            response = self.client.delete(f'/course-subscriptions/{row.pk}/delete')
        self.assertEqual(response.status_code, 500)
        row = CourseSubscription._base_manager.get(pk=row.pk)
        self.assertFalse(row.deleted)
        self.assertEqual(row.payment_id, payment.pk)
        self.assertTrue(CourseSubscriptionInstallment._base_manager.filter(pk=installment.pk).exists())
        self.assertTrue(Payment._base_manager.filter(pk=payment.pk).exists())
