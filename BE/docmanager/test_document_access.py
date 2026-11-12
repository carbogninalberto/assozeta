"""Documents are served to the association's staff and to the member they belong to."""
import base64
from unittest.mock import patch

from django.conf import settings
from django.http import HttpResponse
from rest_framework.test import APIClient

from application.models import Payment, Subscription, User
from application.models.subscriptions_models import MedicalCertificate, SubscriptionFile
from application.models.user_models import Instructor, InstructorHours, SportAssociationModuleTemplates
from application.services.jwt_token_service import JWTTokenService
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_customer_invoice,
    create_test_invoice,
    create_test_medical_appointment,
    create_test_payment,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)
from docmanager.access import NOT_AVAILABLE, can_access_document
from docmanager.models import Document

PDF = b'%PDF-test'


def pdf_response(*args, **kwargs):
    return HttpResponse(PDF, content_type='application/pdf')


class DocumentAccessTestCase(BaseAPITestCase):
    """An athlete with one subscription, a family member, and another member of the same association."""

    def setUp(self):
        super().setUp()
        self.athlete = create_test_user(role=User.ATHLETE)
        self.own = self.member(user=self.athlete, tax_code='RSSMRA90A01H501U')
        # A later subscription of the same person, entered by the association.
        self.family = self.member(associate=self.own.associate)
        # The same person, by tax code, in the records another association keeps.
        self.elsewhere = self.member(tax_code='RSSMRA90A01H501U', sport_association=create_test_sport_association())
        # A relative the athlete enrolled: another associate, the athlete's user.
        self.relative = self.member(user=self.athlete, tax_code='RSSLCU12A01H501X')
        self.other = self.member(tax_code='BNCGLI85B02F205Z')
        self.collaborator = create_test_user(role=User.COLLABORATOR, connected_user=self.user)
        self.admin = create_test_user(role=User.ATHLETE, is_superuser=True, is_staff=True)

    def member(self, user=None, tax_code=None, associate=None, sport_association=None):
        sport_association = sport_association or self.sport_association
        user = user or sport_association.user
        associate = associate or create_test_associate(sport_association=sport_association, tax_code=tax_code)
        document = Document.objects.create(filename='modulo.pdf')
        subscription = create_test_subscription(sport_association=sport_association, associate=associate, user=user,
                                                document_pdf=document, start_date=f'{2020 + Document.objects.count()}-01-01',
                                                end_date=f'{2020 + Document.objects.count()}-12-31')
        receipt = Document.objects.create(filename='ricevuta.pdf')
        invoice = create_test_invoice(sport_association=sport_association, document_pdf=receipt)
        create_test_payment(sport_association=sport_association, associate=associate, user=user, invoice=invoice)
        subscription.invoice = invoice
        return subscription

    def acting(self, user):
        client = APIClient()
        if user is not None:
            token = JWTTokenService.generate_tokens_for_user(user)['access_token']
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        return client

    def assertNotAvailable(self, response):
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.data, {'detail': NOT_AVAILABLE})


class MemberRecordsTests(DocumentAccessTestCase):
    def test_athlete_reaches_own_and_family_records_only(self):
        for subscription in (self.own, self.family, self.relative, self.elsewhere):
            self.assertTrue(can_access_document(self.athlete, subscription))
            self.assertTrue(can_access_document(self.athlete, subscription.invoice))
            self.assertTrue(can_access_document(self.athlete, subscription.document_pdf))
            self.assertTrue(can_access_document(self.athlete, subscription.invoice.document_pdf))
        self.assertFalse(can_access_document(self.athlete, self.other))
        self.assertFalse(can_access_document(self.athlete, self.other.invoice))
        self.assertFalse(can_access_document(self.athlete, self.other.document_pdf))
        self.assertFalse(can_access_document(self.athlete, self.other.invoice.document_pdf))
        self.assertFalse(can_access_document(self.athlete, Document.objects.create(filename='archivio.pdf')))

    def test_medical_certificates_and_attached_files(self):
        def certificate(subscription, user=None):
            document = Document.objects.create(filename='certificato.pdf')
            medical = MedicalCertificate.objects.create(document=document, user=user)
            if subscription is not None:
                Subscription.objects.filter(pk=subscription.pk).update(medical=medical)
            return document

        def attached(subscription):
            document = Document.objects.create(filename='allegato.pdf')
            SubscriptionFile.objects.create(document=document, subscription=subscription)
            return document

        self.assertTrue(can_access_document(self.athlete, certificate(self.family)))
        self.assertTrue(can_access_document(self.athlete, certificate(None, user=self.athlete)))
        self.assertTrue(can_access_document(self.athlete, attached(self.relative)))
        self.assertFalse(can_access_document(self.athlete, certificate(self.other)))
        self.assertFalse(can_access_document(self.athlete, attached(self.other)))

    def test_blank_tax_codes_do_not_join_members(self):
        blank = create_test_user(role=User.ATHLETE)
        self.member(user=blank, tax_code='')
        unrelated = self.member(tax_code='', sport_association=create_test_sport_association())
        self.assertFalse(can_access_document(blank, unrelated))

    def test_staff_instance_owner_and_administrator_reach_everything(self):
        owner_elsewhere = create_test_user(role=User.ASSOCIATION)
        for user in (self.user, self.collaborator, self.admin, owner_elsewhere):
            self.assertTrue(can_access_document(user, self.other))
            self.assertTrue(can_access_document(user, self.other.invoice))
            self.assertTrue(can_access_document(user, self.other.document_pdf))

    def test_instance_owner_account(self):
        # The primary association's account keeps access whatever its role.
        User.original_objects.filter(pk=self.user.pk).update(role=User.ATHLETE)
        self.user.refresh_from_db()
        self.assertTrue(can_access_document(self.user, self.other.document_pdf))


@patch('docmanager.views.document_view.PrintingService.download_file', side_effect=pdf_response)
class RetrieveDocumentAccessTests(DocumentAccessTestCase):
    def retrieve(self, user, uid, suffix=''):
        return self.acting(user).get(f'/document/retrieve/{uid}{suffix}')

    def test_athlete_downloads_own_and_family_documents(self, download):
        for subscription in (self.own, self.family, self.relative):
            for uid in (subscription.document_pdf_id, subscription.pk, subscription.invoice.document_pdf_id,
                        subscription.invoice.pk):
                self.assertEqual(self.retrieve(self.athlete, uid).status_code, 200)

    def test_athlete_does_not_download_another_member_document(self, download):
        for uid in (self.other.document_pdf_id, self.other.pk, self.other.invoice.document_pdf_id, self.other.invoice.pk):
            self.assertNotAvailable(self.retrieve(self.athlete, uid))
        download.assert_not_called()

    def test_athlete_medical_certificate_by_its_id(self, download):
        document = Document.objects.create(filename='certificato.pdf')
        medical = MedicalCertificate.objects.create(document=document)
        Subscription.objects.filter(pk=self.other.pk).update(medical=medical)
        self.assertNotAvailable(self.retrieve(self.athlete, medical.pk))
        Subscription.objects.filter(pk=self.own.pk).update(medical=medical)
        self.assertEqual(self.retrieve(self.athlete, medical.pk).status_code, 200)

    def test_staff_download_every_document(self, download):
        for user in (self.user, self.collaborator):
            self.assertEqual(self.retrieve(user, self.other.document_pdf_id).status_code, 200)
            self.assertEqual(self.retrieve(user, self.other.invoice.pk).status_code, 200)

    def test_document_token_links_keep_working(self, download):
        document = self.other.document_pdf
        for user in (None, self.athlete):
            self.assertEqual(self.retrieve(user, document.pk, f'?token={document.token}').status_code, 200)
        self.assertEqual(self.retrieve(None, document.pk).status_code, 401)

    def test_query_token_account(self, download):
        token = JWTTokenService.generate_tokens_for_user(self.athlete)['access_token']
        self.assertNotAvailable(self.retrieve(None, self.other.document_pdf_id, f'?token={token}'))
        self.assertEqual(self.retrieve(None, self.own.document_pdf_id, f'?token={token}').status_code, 200)


class RenderedPageAccessTests(DocumentAccessTestCase):
    def test_subscription_page(self):
        self.assertNotAvailable(self.acting(self.athlete).get(f'/document/subscription/{self.other.pk}/view/'))
        for user, subscription in ((self.athlete, self.own), (self.athlete, self.family), (self.user, self.other),
                                   (self.collaborator, self.other)):
            response = self.acting(user).get(f'/document/subscription/{subscription.pk}/view/')
            self.assertEqual(response.status_code, 200, response.content)

    def test_invoice_page(self):
        self.assertNotAvailable(self.acting(self.athlete).get(f'/document/invoice/{self.other.invoice.pk}/view/'))
        for user, subscription in ((self.athlete, self.own), (self.athlete, self.relative), (self.user, self.other)):
            response = self.acting(user).get(f'/document/invoice/{subscription.invoice.pk}/view/')
            self.assertEqual(response.status_code, 200, response.content)

    def test_invoice_page_for_the_renderer_header(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=settings.DOCUMENT_BYPASS_TOKEN)
        response = client.get(f'/document/invoice/{self.other.invoice.pk}/view/')
        self.assertEqual(response.status_code, 200, response.content)


@patch('docmanager.views.printing_views.PrintingService.print_and_store_pdf', side_effect=pdf_response)
class GeneratedDocumentAccessTests(DocumentAccessTestCase):
    def test_athlete_does_not_generate_another_member_documents(self, printed):
        documents = Document.objects.count()
        self.assertNotAvailable(self.acting(self.athlete).get(f'/document/subscription/{self.other.pk}'))
        self.assertNotAvailable(self.acting(self.athlete).get(f'/document/invoice/{self.other.invoice.pk}'))
        self.assertNotAvailable(self.acting(self.athlete).post(f'/document/invoice/{self.other.invoice.pk}'))
        printed.assert_not_called()
        self.assertEqual(Document.objects.count(), documents)

    def test_athlete_generates_own_and_family_documents(self, printed):
        for subscription in (self.own, self.family, self.relative):
            self.assertEqual(self.acting(self.athlete).get(f'/document/subscription/{subscription.pk}').status_code, 200)
            self.assertEqual(self.acting(self.athlete).get(f'/document/invoice/{subscription.invoice.pk}').status_code, 200)

    def test_staff_generate_every_document(self, printed):
        for user in (self.user, self.collaborator):
            self.assertEqual(self.acting(user).get(f'/document/subscription/{self.other.pk}').status_code, 200)
            self.assertEqual(self.acting(user).get(f'/document/invoice/{self.other.invoice.pk}').status_code, 200)

    def test_renderer_header_and_document_token_generate_receipts(self, printed):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=settings.DOCUMENT_BYPASS_TOKEN)
        self.assertEqual(client.get(f'/document/invoice/{self.other.invoice.pk}').status_code, 200)
        # Each generation replaces the receipt: the link carries the current document's token.
        self.other.invoice.refresh_from_db()
        token = self.other.invoice.document_pdf.token
        self.assertEqual(APIClient().get(f'/document/invoice/{self.other.invoice.pk}?token={token}').status_code, 200)

    def test_athlete_payment_list_generates_the_missing_receipts(self, printed):
        invoice = self.relative.invoice
        type(invoice).objects.filter(pk=invoice.pk).update(document_pdf=None)
        type(invoice).objects.filter(pk=self.other.invoice.pk).update(document_pdf=None)
        from application.models import Payment
        Payment.objects.filter(invoice__in=[invoice, self.other.invoice]).update(paid=True)
        self.assertEqual(self.acting(self.athlete).get('/payment/list').status_code, 200)
        invoice.refresh_from_db()
        self.other.invoice.refresh_from_db()
        self.assertIsNotNone(invoice.document_pdf_id)
        self.assertIsNone(self.other.invoice.document_pdf_id)


class DeleteDocumentAccessTests(DocumentAccessTestCase):
    def test_athlete_does_not_delete_documents(self):
        for document in (self.other.document_pdf, self.own.document_pdf):
            response = self.acting(self.athlete).delete(f'/document/{document.pk}/delete')
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.data, {'error': 'Document not found.'})
            self.assertTrue(Document.objects.filter(pk=document.pk).exists())

    def test_staff_delete_the_association_documents(self):
        document = self.other.document_pdf
        self.assertEqual(self.acting(self.user).delete(f'/document/{document.pk}/delete').status_code, 200)
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())


@patch('application.views.subscriptions_views.default_storage.save')
class SubscriptionUploadAccessTests(DocumentAccessTestCase):
    BODY = {'document': base64.b64encode(b'file').decode(), 'filename': 'allegato.txt'}

    def upload(self, user, subscription):
        return self.acting(user).post(f'/subscription/{subscription.pk}/upload-document', self.BODY, format='json')

    def test_athlete_does_not_attach_files_to_another_member(self, save):
        documents = Document.objects.count()
        self.assertNotAvailable(self.upload(self.athlete, self.other))
        save.assert_not_called()
        self.assertEqual(Document.objects.count(), documents)
        self.assertFalse(SubscriptionFile.objects.filter(subscription=self.other).exists())

    def test_athlete_attaches_files_to_own_and_family_subscriptions(self, save):
        for subscription in (self.own, self.family, self.relative):
            self.assertEqual(self.upload(self.athlete, subscription).status_code, 200)
            self.assertEqual(SubscriptionFile.objects.filter(subscription=subscription).count(), 1)

    def test_staff_attach_files_to_every_subscription(self, save):
        for user in (self.user, self.collaborator):
            self.assertEqual(self.upload(user, self.other).status_code, 200)
        self.assertEqual(SubscriptionFile.objects.filter(subscription=self.other).count(), 2)


class StaffDocumentsTestCase(DocumentAccessTestCase):
    """Customer invoices, compensations, module templates and medical appointments of the association."""

    def setUp(self):
        super().setUp()
        self.instructor = Instructor.objects.create(user=self.user, first_name='Ada', last_name='Neri')
        self.compensation = self.compensated(self.instructor)
        self.customer_invoice = create_test_customer_invoice(
            sport_association=self.sport_association, pdf=Document.objects.create(filename='fattura.pdf'))
        self.template = SportAssociationModuleTemplates.objects.create(
            sport_association=self.sport_association, name='Liberatoria', template='<p>Liberatoria</p>')
        self.own_visit = create_test_medical_appointment(
            subscription=self.own, document=Document.objects.create(filename='visita.pdf'))
        self.other_visit = create_test_medical_appointment(
            subscription=self.other, document=Document.objects.create(filename='visita.pdf'))

    def compensated(self, instructor):
        payment = create_test_payment(sport_association=self.sport_association, associate=self.other.associate,
                                      instructor=instructor, subject=Payment.OTHER)
        InstructorHours.objects.create(instructor=instructor, hours=2, payment=payment,
                                       document=Document.objects.create(filename='compenso.pdf'))
        return payment

    def tied_account(self, instructor):
        account = create_test_user(role=User.ATHLETE)
        Instructor.objects.filter(pk=instructor.pk).update(associated_user_id=account.pk)
        return account

    def renderer(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=settings.DOCUMENT_BYPASS_TOKEN)
        return client

    def staff_pages(self):
        return (f'/document/einvoice/{self.customer_invoice.pk}/view/', f'/document/compensation/{self.compensation.pk}/view/',
                f'/document/template/{self.template.pk}/view/')

    def staff_printed(self):
        return (f'/document/einvoice/{self.customer_invoice.pk}', f'/document/compensation/{self.compensation.pk}')


class StaffRecordsTests(StaffDocumentsTestCase):
    def test_staff_records_and_compensations(self):
        account = self.tied_account(self.instructor)
        for record in (self.customer_invoice, self.template, self.compensation):
            self.assertFalse(can_access_document(self.athlete, record))
            for user in (self.user, self.collaborator, self.admin):
                self.assertTrue(can_access_document(user, record))
        self.assertTrue(can_access_document(account, self.compensation))
        self.assertFalse(can_access_document(account, self.customer_invoice))
        self.assertFalse(can_access_document(account, self.template))


@patch('docmanager.views.printing_views.generate_invoice_html', return_value='<html></html>')
class StaffPageAccessTests(StaffDocumentsTestCase):
    def test_member_account_does_not_open_staff_pages(self, html):
        for path in self.staff_pages():
            self.assertNotAvailable(self.acting(self.athlete).get(path))
        html.assert_not_called()

    def test_staff_open_every_page(self, html):
        for user in (self.user, self.collaborator):
            for path in self.staff_pages():
                self.assertEqual(self.acting(user).get(path).status_code, 200, path)

    def test_template_page_with_a_subscription(self, html):
        path = f'/document/template/{self.template.pk}/view/?subscription_id={self.own.pk}'
        self.assertNotAvailable(self.acting(self.athlete).get(path))
        self.assertEqual(self.acting(self.user).get(path).status_code, 200)

    def test_renderer_header_pages(self, html):
        for path in (f'/document/einvoice/{self.customer_invoice.pk}/view/', f'/document/template/{self.template.pk}/view/',
                     f'/document/medical-appointment/{self.other_visit.pk}/view/'):
            self.assertEqual(self.renderer().get(path).status_code, 200, path)

    def test_account_tied_to_the_instructor_opens_its_compensation(self, html):
        account = self.tied_account(self.instructor)
        elsewhere = self.compensated(Instructor.objects.create(user=self.user, first_name='Ugo', last_name='Bini'))
        self.assertEqual(self.acting(account).get(f'/document/compensation/{self.compensation.pk}/view/').status_code, 200)
        self.assertNotAvailable(self.acting(account).get(f'/document/compensation/{elsewhere.pk}/view/'))
        self.assertNotAvailable(self.acting(account).get(f'/document/einvoice/{self.customer_invoice.pk}/view/'))

    def test_medical_appointment_page(self, html):
        own, other = (f'/document/medical-appointment/{visit.pk}/view/' for visit in (self.own_visit, self.other_visit))
        response = APIClient().get(other)
        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(APIClient().get(f'{other}?token={self.other_visit.document.token}').status_code, 401)
        self.assertNotAvailable(self.acting(self.athlete).get(other))
        self.assertEqual(self.acting(self.athlete).get(own).status_code, 200)
        for user in (self.user, self.collaborator):
            self.assertEqual(self.acting(user).get(other).status_code, 200)

    def test_medical_appointment_page_keeps_the_other_regions_answer(self, html):
        visit = create_test_medical_appointment(subscription=self.other, region='agonistica-piemonte-biella')
        response = self.acting(self.user).get(f'/document/medical-appointment/{visit.pk}/view/')
        self.assertEqual(response.status_code, 303)


@patch('docmanager.views.printing_views.PrintingService.print_and_store_pdf', side_effect=pdf_response)
class StaffGeneratedAccessTests(StaffDocumentsTestCase):
    def test_member_account_does_not_generate_staff_documents(self, printed):
        documents = Document.objects.count()
        for path in self.staff_printed():
            self.assertNotAvailable(self.acting(self.athlete).get(path))
            self.assertNotAvailable(self.acting(self.athlete).post(path))
        printed.assert_not_called()
        self.assertEqual(Document.objects.count(), documents)

    def test_staff_generate_staff_documents(self, printed):
        for user in (self.user, self.collaborator):
            for path in self.staff_printed():
                self.assertEqual(self.acting(user).get(path).status_code, 200, path)

    def test_print_tasks_keep_generating_customer_invoices(self, printed):
        self.assertEqual(self.renderer().get(f'/document/einvoice/{self.customer_invoice.pk}').status_code, 200)
        self.customer_invoice.refresh_from_db()
        self.assertEqual(self.customer_invoice.pdf.filename.split('] ')[1][:7], 'Fattura')

    def test_account_tied_to_the_instructor_generates_its_compensation(self, printed):
        account = self.tied_account(self.instructor)
        elsewhere = self.compensated(Instructor.objects.create(user=self.user, first_name='Ugo', last_name='Bini'))
        self.assertEqual(self.acting(account).get(f'/document/compensation/{self.compensation.pk}').status_code, 200)
        self.assertNotAvailable(self.acting(account).get(f'/document/compensation/{elsewhere.pk}'))

    def test_medical_appointment_documents(self, printed):
        documents = Document.objects.count()
        self.assertNotAvailable(self.acting(self.athlete).get(f'/document/medical-appointment/{self.other_visit.pk}'))
        self.assertEqual(Document.objects.count(), documents)
        self.assertEqual(self.acting(self.athlete).get(f'/document/medical-appointment/{self.own_visit.pk}').status_code, 200)
        for user in (self.user, self.collaborator):
            self.assertEqual(self.acting(user).get(f'/document/medical-appointment/{self.other_visit.pk}').status_code, 200)

    def test_medical_appointment_list_generates_the_missing_documents(self, printed):
        # The list prints through the task, with the caller's Authorization header.
        visit = create_test_medical_appointment(subscription=self.other)
        token = JWTTokenService.generate_tokens_for_user(self.user)['access_token']

        def forward(url, headers):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=headers['Authorization'])
            return client.get(url.replace(settings.CURRENT_HOST, ''))

        with patch('application.printing_tasks.requests.get', side_effect=forward):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
            response = client.get(f'/subscription/{self.other.pk}/medical-appointments/list')
        self.assertEqual(response.status_code, 200, response.content)
        visit.refresh_from_db()
        self.assertIsNotNone(visit.document_id)

    def test_form_filled_medical_appointment(self, printed):
        # The regional PDF forms are filled in place: no page is rendered.
        visit = create_test_medical_appointment(subscription=self.other, region='agonistica-piemonte-biella')
        with patch('docmanager.views.printing_views.get_pdf_fields', return_value=[{'associate-first_name': ''}]), \
                patch('docmanager.views.printing_views.PrintingService.fill_pdf_and_store', side_effect=pdf_response) as filled:
            self.assertNotAvailable(self.acting(self.athlete).get(f'/document/medical-appointment/{visit.pk}'))
            filled.assert_not_called()
            response = self.acting(self.user).get(f'/document/medical-appointment/{visit.pk}')
        self.assertEqual(response.status_code, 200, response.content)
        filled.assert_called_once()
        printed.assert_not_called()
        visit.refresh_from_db()
        self.assertIsNotNone(visit.document_id)


@patch('docmanager.views.printing_views.PrintingService.print_and_store_pdf', side_effect=pdf_response)
class DocumentTokenTests(StaffDocumentsTestCase):
    """?token admits the request when it is the token of the record's own document."""

    def records(self):
        hours = InstructorHours.objects.get(payment=self.compensation)
        return ((f'/document/invoice/{self.other.invoice.pk}', self.other.invoice.document_pdf),
                (f'/document/einvoice/{self.customer_invoice.pk}', self.customer_invoice.pdf),
                (f'/document/compensation/{self.compensation.pk}', hours.document),
                (f'/document/medical-appointment/{self.other_visit.pk}', self.other_visit.document))

    def test_token_of_the_record_document(self, printed):
        for path, document in self.records():
            self.assertEqual(APIClient().get(f'{path}?token={document.token}').status_code, 200, path)

    def test_token_of_another_document(self, printed):
        documents = Document.objects.count()
        for path, _ in self.records():
            for token in (self.own.document_pdf.token, self.own.invoice.document_pdf.token, 'not-a-token', ''):
                response = APIClient().get(f'{path}?token={token}')
                self.assertEqual(response.status_code, 401, (path, token, response.content))
        printed.assert_not_called()
        self.assertEqual(Document.objects.count(), documents)

    def test_record_without_a_document(self, printed):
        token = self.own.document_pdf.token
        type(self.other.invoice).objects.filter(pk=self.other.invoice.pk).update(document_pdf=None)
        self.assertEqual(APIClient().get(f'/document/invoice/{self.other.invoice.pk}?token={token}').status_code, 401)
        self.assertEqual(APIClient().get(f'/document/invoice/not-an-id?token={token}').status_code, 401)

    def test_templates_are_generated_by_accounts(self, printed):
        response = APIClient().post(f'/document/template/{self.template.pk}?token={self.own.document_pdf.token}')
        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(self.acting(self.user).post(f'/document/template/{self.template.pk}').status_code, 200)

    def test_member_account_with_a_token_keeps_its_own_records(self, printed):
        token = self.other.invoice.document_pdf.token
        self.assertNotAvailable(self.acting(self.athlete).get(f'/document/invoice/{self.other.invoice.pk}?token={token}'))


@patch('docmanager.views.document_view.default_storage.save')
class MedicalCertificateUploadTests(DocumentAccessTestCase):
    """The registration form sends the certificate before an account exists."""

    def upload(self, client):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return client.post('/document/medical/certificate/',
                           {'medical_certificate': SimpleUploadedFile('certificato.pdf', PDF, 'application/pdf')}, format='multipart')

    def test_registration_form_upload(self, save):
        response = self.upload(APIClient())
        self.assertEqual(response.status_code, 200, response.content)
        medical = MedicalCertificate.objects.get(pk=response.data['uid'])
        self.assertIsNone(medical.user_id)
        save.assert_called_once()

    def test_account_upload_is_its_own(self, save):
        response = self.upload(self.acting(self.athlete))
        self.assertEqual(response.status_code, 200, response.content)
        medical = MedicalCertificate.objects.get(pk=response.data['uid'])
        self.assertEqual(medical.user_id, self.athlete.pk)
        self.assertTrue(can_access_document(self.athlete, medical.document))
