"""Medical workflows against the real handlers, database and temporary file storage.

These are regression tests, not browser evidence. The only mocked actions are
external dispatch and the seed's production-database guard; the seed runs inside
the test's transaction. All successful API/business outcomes remain real.
"""
import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import fixture_id
from application.models import Associate, AssociateImportDraft, MedicalCertificate, SportAssociation, Subscription, User
from application.tests.base import BaseTestCase
from docmanager.models import Document


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ManualMedicalTests(BaseTestCase):
    PDF = b'%PDF-1.4\nFictional certificate for application testing only.\n%%EOF\n'

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.storage = FileSystemStorage(location=self.root / 'files')
        for module in ('application.services.subscription_service',
                       'application.views.subscriptions_views'):
            for name, value in (('default_storage', self.storage), ('STORAGE_DIR', 'medical-test')):
                replacement = patch(f'{module}.{name}', value)
                replacement.start()
                self.addCleanup(replacement.stop)
        queue = patch('application.signals.check_workflows_trigger.delay')
        self.workflow_queue = queue.start()
        self.addCleanup(queue.stop)
        email_queue = patch('application.views.subscriptions_views.send_email_template.delay')
        self.email_queue = email_queue.start()
        self.addCleanup(email_queue.stop)
        self.seed()
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.subscription = Subscription.objects.get(pk=fixture_id('subscription-1'))
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def seed(self):
        with patch('application.management.commands.seed_manuale.assert_disposable'):
            call_command('seed_manuale', output=str(self.root / 'browser.json'),
                         origin='http://127.0.0.1:5010', verbosity=0)

    def endpoint(self, action, target_id=None):
        return f'/subscription/{target_id or self.subscription.pk}/medical-certificate/{action}'

    def upload(self, filename='certificato-demo.pdf', content=None, target_id=None, **headers):
        return self.client.post(self.endpoint('upload', target_id), {'medical_certificate':
            SimpleUploadedFile(filename, content or self.PDF, content_type='application/pdf')}, format='multipart', **headers)

    def storage_path(self, document):
        return os.path.join('medical-test', str(document.creation_date.timestamp()), str(document.pk), document.filename)

    def assert_no_dispatch(self):
        self.email_queue.assert_not_called()
        self.workflow_queue.assert_not_called()
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)

    def file_snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file() and 'files' in path.parts}

    def foreign_association(self):
        owner = User.objects.create_user(username='medical-foreign-owner', role=User.ASSOCIATION,
                                        email='foreign@example.test')
        association = SportAssociation.objects.create(user=owner, denomination='Foreign association')
        person = Associate.objects.create(sport_association=association, first_name='Foreign', last_name='Person',
                                          email='athlete@foreign.example.test')
        return owner, association, person

    def test_foreign_subscription_mutations_are_denied_before_records_files_or_notifications_change(self):
        foreign_owner, association, person = self.foreign_association()
        foreign_subscription = Subscription.objects.create(sport_association=association, associate=person,
            user=foreign_owner, status_flag=Subscription.ACCEPTED)
        self.client.force_authenticate(user=foreign_owner)
        uploaded = self.upload('foreign.pdf', target_id=foreign_subscription.pk)
        self.assertEqual(uploaded.status_code, 200, uploaded.content)
        dated = self.client.post(self.endpoint('set-certificate-expiration', foreign_subscription.pk),
                                {'certificate_expiring_date': '30/09/2027'}, format='json')
        self.assertEqual(dated.status_code, 200, dated.content)
        foreign_subscription.refresh_from_db()
        medical = foreign_subscription.medical
        before_subscription = Subscription._base_manager.filter(pk=foreign_subscription.pk).values().get()
        before_medical = MedicalCertificate.objects.filter(pk=medical.pk).values().get()
        before_person = Associate._base_manager.filter(pk=person.pk).values().get()
        files = self.file_snapshot()
        counts = (MedicalCertificate.objects.count(), Document.objects.count())
        self.client.force_authenticate(user=self.owner)
        denied = self.upload('denied-cross-association.pdf', target_id=foreign_subscription.pk)
        self.assertEqual(denied.status_code, 404, denied.content)
        for action, payload in (
            ('set-certificate-expiration', {'certificate_expiring_date': '01/10/2028'}),
            ('edit', {'notes': 'Denied', 'competitive_medical_certificate': True}),
            ('set-certificate-expiration', {'certificate_expiring_date': None}),
            ('send-email-reminder', {}),
        ):
            with self.subTest(action=action, payload=payload):
                denied = self.client.post(self.endpoint(action, foreign_subscription.pk), payload, format='json')
                self.assertEqual(denied.status_code, 404, denied.content)
        self.assertEqual(Subscription._base_manager.filter(pk=foreign_subscription.pk).values().get(), before_subscription)
        self.assertEqual(MedicalCertificate.objects.filter(pk=medical.pk).values().get(), before_medical)
        self.assertEqual(Associate._base_manager.filter(pk=person.pk).values().get(), before_person)
        self.assertEqual((MedicalCertificate.objects.count(), Document.objects.count()), counts)
        self.assertEqual(self.file_snapshot(), files)
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.medical_id)
        self.assert_no_dispatch()

    def test_owned_draft_upload_and_date_persist_but_foreign_draft_cannot_be_mutated(self):
        draft = AssociateImportDraft.objects.create(sport_association=self.subscription.sport_association, data={})
        uploaded = self.upload('draft.pdf', target_id=draft.pk)
        self.assertEqual(uploaded.status_code, 200, uploaded.content)
        self.assertEqual(uploaded.data['msg'], 'updated draft')
        draft.refresh_from_db()
        medical = MedicalCertificate.objects.get(pk=draft.data['medical_certificate']['medical_id'])
        self.assertEqual(draft.data['medical_certificate']['filename'], 'draft.pdf')
        with self.storage.open(self.storage_path(medical.document), 'rb') as stored:
            self.assertEqual(stored.read(), self.PDF)
        dated = self.client.post(self.endpoint('set-certificate-expiration', draft.pk),
                                {'certificate_expiring_date': '30/09/2027'}, format='json')
        self.assertEqual(dated.status_code, 200, dated.content)
        medical.refresh_from_db()
        self.assertEqual(medical.expiration_date, date(2027, 9, 30))
        _, association, _ = self.foreign_association()
        foreign_draft = AssociateImportDraft.objects.create(sport_association=association,
            data={'medical_certificate': {'medical_id': str(medical.pk), 'filename': 'draft.pdf'}})
        before = AssociateImportDraft.objects.filter(pk=foreign_draft.pk).values().get()
        medical_before = MedicalCertificate.objects.filter(pk=medical.pk).values().get()
        files = self.file_snapshot()
        counts = (MedicalCertificate.objects.count(), Document.objects.count())
        denied_upload = self.upload('denied-draft.pdf', target_id=foreign_draft.pk)
        self.assertEqual(denied_upload.status_code, 404, denied_upload.content)
        for expiration in ('01/10/2028', None):
            denied = self.client.post(self.endpoint('set-certificate-expiration', foreign_draft.pk),
                                     {'certificate_expiring_date': expiration}, format='json')
            self.assertEqual(denied.status_code, 404, denied.content)
        self.assertEqual(AssociateImportDraft.objects.filter(pk=foreign_draft.pk).values().get(), before)
        self.assertEqual(MedicalCertificate.objects.filter(pk=medical.pk).values().get(), medical_before)
        self.assertEqual((MedicalCertificate.objects.count(), Document.objects.count()), counts)
        self.assertEqual(self.file_snapshot(), files)
        # Editing a permitted draft also isolates a certificate referenced by
        # a foreign draft's JSON; the foreign original stays unchanged.
        own_edit = self.client.post(self.endpoint('set-certificate-expiration', draft.pk),
                                   {'certificate_expiring_date': '01/10/2028'}, format='json')
        self.assertEqual(own_edit.status_code, 200, own_edit.content)
        draft.refresh_from_db()
        self.assertNotEqual(draft.data['medical_certificate']['medical_id'], str(medical.pk))
        clone = MedicalCertificate.objects.get(pk=draft.data['medical_certificate']['medical_id'])
        self.assertEqual(clone.expiration_date, date(2028, 10, 1))
        self.assertEqual(clone.document_id, medical.document_id)
        self.assertEqual(MedicalCertificate.objects.filter(pk=medical.pk).values().get(), medical_before)
        self.assertEqual(AssociateImportDraft.objects.filter(pk=foreign_draft.pk).values().get(), before)
        self.assert_no_dispatch()

    def test_missing_or_malformed_upload_target_cannot_create_orphan_records_or_files(self):
        for target, expected_status in ((fixture_id('nonexistent-medical-target'), 404), ('malformed-id', 400)):
            with self.subTest(target=target):
                response = self.upload(target_id=target)
                self.assertEqual(response.status_code, expected_status, response.content)
                self.assertEqual(MedicalCertificate.objects.count(), 0)
                self.assertEqual(Document.objects.count(), 0)
                self.assertEqual(self.file_snapshot(), {})
        self.assert_no_dispatch()

    def test_shared_foreign_hidden_certificate_and_document_survive_owner_edit_date_and_removal(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.content)
        self.subscription.refresh_from_db()
        original = self.subscription.medical
        original.expiration_date = date(2027, 9, 30)
        original.notes = 'Shared original'
        original.competitive_medical_certificate = True
        original.save()
        foreign_owner, association, person = self.foreign_association()
        foreign = Subscription.objects.create(sport_association=association, associate=person,
            user=foreign_owner, medical=original, document_pdf=original.document, deleted=True)
        before_foreign = Subscription._base_manager.filter(pk=foreign.pk).values().get()
        before_medical = MedicalCertificate.objects.filter(pk=original.pk).values().get()
        files = self.file_snapshot()
        for action, payload in (
            ('edit', {'notes': 'Owner only', 'competitive_medical_certificate': False}),
            ('set-certificate-expiration', {'certificate_expiring_date': '01/10/2028'}),
            ('set-certificate-expiration', {'certificate_expiring_date': None}),
        ):
            with self.subTest(action=action, payload=payload):
                self.subscription.medical = original
                self.subscription.save(update_fields=['medical'])
                updated = self.client.post(self.endpoint(action), payload, format='json')
                self.assertEqual(updated.status_code, 200, updated.content)
                self.subscription.refresh_from_db()
                self.assertNotEqual(self.subscription.medical_id, original.pk)
                if action == 'edit':
                    self.assertEqual(self.subscription.medical.notes, 'Owner only')
                    self.assertFalse(self.subscription.medical.competitive_medical_certificate)
                elif payload['certificate_expiring_date'] is not None:
                    self.assertEqual(self.subscription.medical.expiration_date, date(2028, 10, 1))
                else:
                    self.assertIsNone(self.subscription.medical_id)
                self.assertEqual(Subscription._base_manager.filter(pk=foreign.pk).values().get(), before_foreign)
                self.assertEqual(MedicalCertificate.objects.filter(pk=original.pk).values().get(), before_medical)
                self.assertTrue(Document.objects.filter(pk=original.document_id).exists())
                self.assertEqual(self.file_snapshot(), files)
        self.assert_no_dispatch()

    def test_same_association_shared_certificate_keeps_existing_shared_edit_behavior(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.content)
        self.subscription.refresh_from_db()
        original = self.subscription.medical
        second = Subscription.objects.get(pk=fixture_id('subscription-2'))
        second.medical = original
        second.save(update_fields=['medical'])
        edited = self.client.post(self.endpoint('edit'), {'notes': 'Same association'}, format='json')
        self.assertEqual(edited.status_code, 200, edited.content)
        second.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.medical_id, original.pk)
        self.assertEqual(second.medical_id, original.pk)
        self.assertEqual(second.medical.notes, 'Same association')
        self.assertEqual(MedicalCertificate.objects.count(), 1)
        self.assert_no_dispatch()

    def test_owner_impersonating_athlete_keeps_home_medical_access_and_foreign_boundary(self):
        athlete = User.objects.create_user(username='medical-athlete', role=User.ATHLETE, email='athlete@example.test')
        self.subscription.associate.user = athlete
        self.subscription.associate.save(update_fields=['user'])
        self.subscription.user = athlete
        self.subscription.save(update_fields=['user'])
        _, association, person = self.foreign_association()
        person.user = athlete
        person.save(update_fields=['user'])
        foreign = Subscription.objects.create(user=athlete, sport_association=association, associate=person)
        session = self.client.post('/association/impersonation', {'target_user_id': str(athlete.pk)}, format='json')
        self.assertEqual(session.status_code, 201, session.content)
        headers = {'HTTP_X_IMPERSONATION_ID': session.data['session_id'], 'HTTP_USER_ID': str(athlete.pk)}
        uploaded = self.upload(**headers)
        self.assertEqual(uploaded.status_code, 200, uploaded.content)
        self.subscription.refresh_from_db()
        medical = self.subscription.medical
        self.assertEqual(medical.user_id, athlete.pk)
        dated = self.client.post(self.endpoint('set-certificate-expiration'),
                                {'certificate_expiring_date': '30/09/2027'}, format='json', **headers)
        self.assertEqual(dated.status_code, 200, dated.content)
        edited = self.client.post(self.endpoint('edit'), {'notes': 'Scoped athlete'}, format='json', **headers)
        self.assertEqual(edited.status_code, 200, edited.content)
        medical.refresh_from_db()
        self.assertEqual(medical.expiration_date, date(2027, 9, 30))
        self.assertEqual(medical.notes, 'Scoped athlete')
        files = self.file_snapshot()
        denied = self.upload('denied-impersonation.pdf', target_id=foreign.pk, **headers)
        self.assertEqual(denied.status_code, 403, denied.content)
        foreign.refresh_from_db()
        self.assertIsNone(foreign.medical_id)
        self.assertEqual(self.file_snapshot(), files)
        removed = self.client.post(self.endpoint('set-certificate-expiration'),
                                  {'certificate_expiring_date': None}, format='json', **headers)
        self.assertEqual(removed.status_code, 200, removed.content)
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.medical_id)
        self.assertTrue(MedicalCertificate.objects.filter(pk=medical.pk).exists())
        self.assertTrue(Document.objects.filter(pk=medical.document_id).exists())
        self.assert_no_dispatch()

    def test_real_upload_date_edit_and_removal(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.data['expiring_date'])
        self.subscription.refresh_from_db()
        medical = self.subscription.medical
        document = medical.document
        self.assertEqual(str(response.data['medical']), str(document.pk))
        self.assertEqual(medical.user_id, self.owner.pk)
        self.assertIsNone(medical.expiration_date)
        self.assertEqual(document.filename, 'certificato-demo.pdf')
        path = self.storage_path(document)
        with self.storage.open(path, 'rb') as stored:
            self.assertEqual(stored.read(), self.PDF)

        dated = self.client.post(self.endpoint('set-certificate-expiration'),
                                 {'certificate_expiring_date': '30/09/2027'}, format='json')
        self.assertEqual(dated.status_code, 200, dated.content)
        medical.refresh_from_db()
        self.assertEqual(medical.expiration_date, date(2027, 9, 30))
        edited = self.client.post(self.endpoint('edit'),
            {'competitive_medical_certificate': True, 'notes': 'Documento dimostrativo verificato'}, format='json')
        self.assertEqual(edited.status_code, 200, edited.content)
        medical.refresh_from_db()
        self.assertTrue(medical.competitive_medical_certificate)
        self.assertEqual(medical.notes, 'Documento dimostrativo verificato')
        self.assertEqual(medical.document_id, document.pk)

        removed = self.client.post(self.endpoint('set-certificate-expiration'),
                                   {'certificate_expiring_date': None}, format='json')
        self.assertEqual(removed.status_code, 200, removed.content)
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.medical_id)
        self.assertFalse(MedicalCertificate.objects.filter(pk=medical.pk).exists())
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())
        # Existing behavior removes the association and DB rows, but retains
        # stored bytes. Do not describe this handler as physical file erasure.
        self.assertTrue(self.storage.exists(path))
        self.assert_no_dispatch()

    def test_reader_cannot_upload_set_date_edit_or_remove_and_does_not_write_files(self):
        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 200, uploaded.content)
        self.subscription.refresh_from_db()
        medical = self.subscription.medical
        files_before = list((self.root / 'files').rglob('*'))
        self.client.force_authenticate(user=self.reader)
        denied_upload = self.upload('denied.pdf')
        self.assertEqual(denied_upload.status_code, 403, denied_upload.content)
        for action, payload in (
            ('set-certificate-expiration', {'certificate_expiring_date': '30/09/2027'}),
            ('edit', {'competitive_medical_certificate': True, 'notes': 'denied'}),
            ('set-certificate-expiration', {'certificate_expiring_date': None}),
        ):
            with self.subTest(action=action, payload=payload):
                denied = self.client.post(self.endpoint(action), payload, format='json')
                self.assertEqual(denied.status_code, 403, denied.content)
        self.subscription.refresh_from_db()
        medical.refresh_from_db()
        self.assertEqual(self.subscription.medical_id, medical.pk)
        self.assertIsNone(medical.expiration_date)
        self.assertIsNone(medical.notes)
        self.assertFalse(medical.competitive_medical_certificate)
        self.assertEqual(MedicalCertificate.objects.count(), 1)
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(list((self.root / 'files').rglob('*')), files_before)
        self.assert_no_dispatch()

    def test_upload_without_file_has_no_certificate_document_or_attachment(self):
        response = self.client.post(self.endpoint('upload'), {}, format='multipart')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('medical_certificate', response.data['error'])
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.medical_id)
        self.assertEqual(MedicalCertificate.objects.count(), 0)
        self.assertEqual(Document.objects.count(), 0)
        self.assertFalse((self.root / 'files').exists())
        self.assert_no_dispatch()

    def test_date_without_upload_creates_generated_png_placeholder(self):
        response = self.client.post(self.endpoint('set-certificate-expiration'),
                                    {'certificate_expiring_date': '30/09/2027'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.subscription.refresh_from_db()
        medical = self.subscription.medical
        self.assertEqual(medical.expiration_date, date(2027, 9, 30))
        self.assertEqual(medical.user_id, self.owner.pk)
        self.assertTrue(medical.document.filename.endswith('.png'))
        with self.storage.open(self.storage_path(medical.document), 'rb') as stored:
            self.assertTrue(stored.read().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assert_no_dispatch()

    def test_replacement_attaches_new_real_file_but_preserves_old_unlinked_record(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.content)
        self.subscription.refresh_from_db()
        original = self.subscription.medical
        replacement_bytes = self.PDF + b'replacement'
        replacement = self.upload('certificato-sostitutivo.pdf', replacement_bytes)
        self.assertEqual(replacement.status_code, 200, replacement.content)
        self.subscription.refresh_from_db()
        self.assertNotEqual(self.subscription.medical_id, original.pk)
        self.assertIsNone(self.subscription.medical.expiration_date)
        with self.storage.open(self.storage_path(self.subscription.medical.document), 'rb') as stored:
            self.assertEqual(stored.read(), replacement_bytes)
        self.assertTrue(MedicalCertificate.objects.filter(pk=original.pk).exists())
        self.assertTrue(Document.objects.filter(pk=original.document_id).exists())
        self.assertTrue(self.storage.exists(self.storage_path(original.document)))
        self.assert_no_dispatch()

    def test_reset_cleans_owned_certificates_preserves_foreign_hidden_refs_and_shared_documents(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.content)
        self.subscription.refresh_from_db()
        removable = self.subscription.medical
        removed_document = removable.document
        self.subscription.notes = 'scenario notes'
        self.subscription.custom_data = {'scenario': 'changed'}
        self.subscription.additional_fields = {'scenario': 'changed'}
        self.subscription.archived = True
        self.subscription.deleted = True
        self.subscription.save()

        foreign_owner = User.objects.create_user(username='medical-preserved-owner', email='preserved@example.test')
        foreign_association = SportAssociation.objects.create(user=foreign_owner, denomination='Preserved association')
        foreign_person = Associate.objects.create(sport_association=foreign_association, first_name='Preserved')
        protected_document = Document.objects.create(filename='foreign-reference.pdf')
        protected = MedicalCertificate.objects.create(user=self.owner, document=protected_document,
            expiration_date=date(2027, 4, 20), notes='protected certificate', competitive_medical_certificate=True)
        nonmedical_shared_document = Document.objects.create(filename='foreign-subscription-document.pdf')
        owned_nonmedical_shared = MedicalCertificate.objects.create(user=self.owner, document=nonmedical_shared_document)
        foreign_subscription = Subscription.objects.create(sport_association=foreign_association,
            associate=foreign_person, user=foreign_owner, medical=protected,
            document_pdf=nonmedical_shared_document, deleted=True)
        shared_document = Document.objects.create(filename='shared-document.pdf')
        owned_unlinked = MedicalCertificate.objects.create(user=self.owner, document=shared_document)
        foreign_medical = MedicalCertificate.objects.create(user=foreign_owner, document=shared_document)
        unlinked_document = Document.objects.create(filename='owned-unlinked.pdf')
        owned_orphan = MedicalCertificate.objects.create(user=self.owner, document=unlinked_document)
        # A foreign-owned certificate on an owned fixture is detached, preserved.
        second = Subscription.objects.get(pk=fixture_id('subscription-2'))
        second.medical = foreign_medical
        second.save(update_fields=['medical'])

        self.seed()
        self.assertFalse(MedicalCertificate.objects.filter(pk__in=[removable.pk, owned_unlinked.pk,
            owned_orphan.pk, owned_nonmedical_shared.pk]).exists())
        self.assertFalse(Document.objects.filter(pk__in=[removed_document.pk, unlinked_document.pk]).exists())
        self.assertTrue(Document.objects.filter(pk=shared_document.pk).exists())
        self.assertTrue(Document.objects.filter(pk=nonmedical_shared_document.pk).exists())
        self.assertTrue(MedicalCertificate.objects.filter(pk=foreign_medical.pk).exists())
        protected.refresh_from_db()
        self.assertEqual(protected.expiration_date, date(2027, 4, 20))
        self.assertEqual(protected.notes, 'protected certificate')
        self.assertTrue(protected.competitive_medical_certificate)
        self.assertEqual(protected.document_id, protected_document.pk)
        foreign_subscription.refresh_from_db()
        self.assertTrue(foreign_subscription.deleted)
        self.assertEqual(foreign_subscription.medical_id, protected.pk)
        self.assertEqual(foreign_subscription.document_pdf_id, nonmedical_shared_document.pk)
        for index in range(1, 4):
            stable = Subscription.objects.get(pk=fixture_id(f'subscription-{index}'))
            self.assertIsNone(stable.medical_id)
            self.assertIsNone(stable.notes)
            self.assertIsNone(stable.custom_data)
            self.assertIsNone(stable.additional_fields)
            self.assertFalse(stable.archived)
            self.assertFalse(stable.deleted)
        self.assert_no_dispatch()
