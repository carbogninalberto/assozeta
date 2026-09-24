import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from application.models import User, SportAssociation, Associate, Family
from application.services.export_service import AssociationExportService
from instance.models import InstanceConfiguration, DataRestore
from instance.restore.archive import Archive, RestoreError
from instance.restore.execution import execute
from instance.restore.locking import operation_lock, Busy


class RestoreTests(TransactionTestCase):
    # Streamed responses and Channels deliberately run connection cleanup. Real
    # committed fixtures are required; TestCase's enclosing transaction is not.
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(
            DATA_RESTORE_LOCK_PATH=self.directory.name + '/lifecycle.lock',
            MEDIA_ROOT=self.directory.name + '/media', STORAGE_DIR='',
            STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                      'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
            CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.owner = User.objects.create_user(username='local-owner', email='local@example.test', password='LocalSecret', role=User.ASSOCIATION)
        self.association = SportAssociation.objects.create(user=self.owner, denomination='Current ASD', tax_code='11111111111')
        self.config = InstanceConfiguration.objects.create(
            domain='local.example.test', name='Local branding', primary_association=self.association,
            email_settings={'host': 'local-smtp'}, email_password_encrypted='keep-encrypted',
            integration_settings={'stripe': {'secret': 'keep'}}, logo_path='instance/local-logo.png',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.endpoint = '/instance/admin/data-restore'
        self.source_owner = str(uuid.uuid4())
        self.source_association = str(self.association.pk)

    def archive(self, extra=None, files=None, mutate=None):
        rows = {item[0].__name__: [] for item in AssociationExportService.EXPORT_ORDER}
        rows['SportAssociation'] = [{'sport_association_id': self.source_association, 'user_id': self.source_owner,
                                      'denomination': 'Bakney ASD', 'tax_code': '22222222222'}]
        rows['User'] = [{'user_id': self.source_owner, 'username': 'bakney-owner', 'email': 'bakney@example.test', 'role': User.ASSOCIATION}]
        rows.update(extra or {})
        manifest = {'version': '1.0.0', 'export_format': 'bakney_sport_export_v1', 'export_date': '2026-09-23T12:00:00Z',
                    'association': {'sport_association_id': self.source_association, 'denomination': 'Bakney ASD'}, 'models_exported': [], 'statistics': {}}
        entries = {}
        for item in AssociationExportService.EXPORT_ORDER:
            name = item[0].__name__
            filename = ('system' if len(item) > 2 else 'data') + '/' + item[1] + '.json'
            manifest['models_exported'].append({'name': name, 'file': filename, 'count': len(rows[name])})
            entries[filename] = json.dumps(rows[name])
        # Actual Bakney v1 exports still declare this retired platform model,
        # including when empty. Assozeta's current exporter no longer does.
        legacy = rows.get('SportAssociationInvoices', [])
        legacy_path = 'data/48_sport_association_invoices.json'
        manifest['models_exported'].append({'name': 'SportAssociationInvoices', 'file': legacy_path, 'count': len(legacy)})
        entries[legacy_path] = json.dumps(legacy)
        entries['manifest.json'] = json.dumps(manifest)
        entries.update(files or {})
        if mutate:
            mutate(entries)
        result = io.BytesIO()
        with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return result.getvalue()

    def upload(self, content=None):
        response = self.client.post(self.endpoint, {'file': SimpleUploadedFile('bakney.zip', content or self.archive())}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        return DataRestore.objects.get(pk=response.data['id'])

    def start(self, op, **kwargs):
        with patch('instance.restore.views.restore_instance_data.apply_async'):
            response = self.client.post(f'{self.endpoint}/{op.id}/start', {'confirmation': 'RIPRISTINA', **kwargs}, format='json')
        self.assertEqual(response.status_code, 202, response.data)
        return op

    def test_replacement_preserves_all_configuration_owner_and_same_uuid(self):
        old_family = Family.objects.create()
        old_associate = Associate.objects.create(sport_association=self.association, family=old_family, first_name='Old')
        config_before = InstanceConfiguration.objects.values().get()
        owner_before = User.original_objects.values().get(pk=self.owner.pk)
        new_associate = str(uuid.uuid4())
        op = self.upload(self.archive({'Associate': [{'associate_id': new_associate, 'sport_association_id': self.source_association, 'first_name': 'New'}]}))
        self.start(op)
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        self.assertEqual(InstanceConfiguration.objects.values().get(), config_before)
        self.assertEqual(User.original_objects.values().get(pk=self.owner.pk), owner_before)
        self.assertEqual(SportAssociation.objects.count(), 1)
        self.association.refresh_from_db()
        self.assertEqual(self.association.denomination, 'Bakney ASD')
        self.assertEqual(self.association.user_id, self.owner.pk)
        self.assertFalse(Associate._base_manager.filter(pk=old_associate.pk).exists())
        self.assertFalse(Family.objects.filter(pk=old_family.pk).exists())
        self.assertTrue(Associate.objects.filter(pk=new_associate, sport_association=self.association).exists())
        self.assertTrue(default_storage.exists(op.backup_path))
        self.assertFalse(op.upload_path)
        response = self.client.get(f'{self.endpoint}/{op.id}/backup')
        self.assertEqual(response.status_code, 200)
        response.close()
        # A duplicate delivery cannot replay the committed replacement.
        self.association.denomination = 'Edited after restore'
        self.association.save()
        execute(op.pk)
        self.association.refresh_from_db()
        self.assertEqual(self.association.denomination, 'Edited after restore')

    def test_failed_import_rolls_back_and_retains_configuration_and_current_data(self):
        old = Associate.objects.create(sport_association=self.association, first_name='Original')
        op = self.start(self.upload())
        with patch('instance.restore.replacement.ReplacementImporter.import_all', side_effect=ValueError('fixture failure')):
            execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        self.assertTrue(Associate.objects.filter(pk=old.pk).exists())
        self.association.refresh_from_db()
        self.assertEqual(self.association.denomination, 'Current ASD')
        self.assertTrue(default_storage.exists(op.backup_path))
        self.config.refresh_from_db()
        self.assertEqual(self.config.name, 'Local branding')

    def test_progress_announces_success_only_after_commit_and_failure_after_rollback(self):
        from django.db import connection
        events = []

        def receive(phase, **kwargs):
            if phase in ('completed', 'failed'):
                self.assertFalse(connection.in_atomic_block)
                events.append(phase)

        op = self.start(self.upload())
        with patch('instance.restore.execution.RestoreProgress.publish', side_effect=receive):
            execute(op.pk)
        self.assertEqual(events, ['completed'])
        events.clear()
        op = self.start(self.upload())
        with patch('instance.restore.execution.RestoreProgress.publish', side_effect=receive), patch(
                'instance.restore.replacement.ReplacementImporter.import_all', side_effect=ValueError('rollback')):
            execute(op.pk)
        self.assertEqual(events, ['failed'])

    def test_requires_owner_even_with_impersonation_header(self):
        op = self.upload()
        outsider = User.objects.create_user(username='outsider', is_superuser=True)
        collaborator = User.objects.create_user(username='collaborator', connected_user=self.owner)
        for user in (None, outsider, collaborator):
            self.client.force_authenticate(user)
            for method, url, data in [('get', self.endpoint, {}), ('post', self.endpoint, {}),
                                       ('post', f'{self.endpoint}/{op.id}/start', {'confirmation': 'RIPRISTINA'}),
                                       ('get', f'{self.endpoint}/{op.id}/backup', {})]:
                response = getattr(self.client, method)(url, data, HTTP_USER_ID=str(self.owner.pk))
                self.assertIn(response.status_code, (401, 403))

    def test_rejects_multi_association_installation_and_keeps_unrelated_data(self):
        other = User.objects.create_user(username='other-owner')
        unrelated = SportAssociation.objects.create(user=other, denomination='Other')
        response = self.client.post(self.endpoint, {'file': SimpleUploadedFile('backup.zip', self.archive())}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(SportAssociation.objects.filter(pk=unrelated.pk).exists())

    def test_confirmation_binds_upload_missing_media_and_hash(self):
        identity = str(uuid.uuid4())
        op = self.upload(self.archive({'Document': [{'document_id': identity, 'filename': 'missing.pdf', 'filepath': 'old/storage.pdf'}]}))
        self.assertEqual(op.preview['missing_media'], 1)
        response = self.client.post(f'{self.endpoint}/{op.id}/start', {'confirmation': 'RIPRISTINA'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.start(op, allow_missing_media=True)
        default_storage.delete(op.upload_path)
        default_storage.save(op.upload_path, ContentFile(b'changed archive'))
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        self.assertIn('cambiato', op.error)
        self.association.refresh_from_db()
        self.assertEqual(self.association.denomination, 'Current ASD')

    def test_rejects_missing_file_path_traversal_and_broken_relationships(self):
        samples = [self.archive(mutate=lambda e: e.pop('data/07_associates.json')),
                   self.archive(files={'files/../../escape': b'bad'}),
                   self.archive({'SportAssociationDocumentsArchive': [{'sport_association_documents_archive_id': str(uuid.uuid4()), 'sport_association_id': self.source_association, 'document_id': str(uuid.uuid4())}]})]
        for content in samples:
            response = self.client.post(self.endpoint, {'file': SimpleUploadedFile('bad.zip', content)}, format='multipart')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(DataRestore.objects.exists())

    def test_missing_optional_links_preserve_certificate_file_and_subscription(self):
        from application.models import MedicalCertificate, Subscription
        from docmanager.models import Document
        medical, document, subscription, associate, missing_user = [str(uuid.uuid4()) for _ in range(5)]
        # Even an existing local account must not be bound to an omitted source user.
        User.objects.create_user(user_id=missing_user, username='unrelated', email='unrelated@example.test')
        op = self.upload(self.archive({
            'Associate': [{'associate_id': associate, 'sport_association_id': self.source_association}],
            'Document': [{'document_id': document, 'filename': 'medical.pdf', 'filepath': 'old/medical.pdf'}],
            'MedicalCertificate': [{'medical_id': medical, 'user_id': missing_user, 'document_id': document}],
            'Subscription': [{'subscription_id': subscription, 'associate_id': associate,
                              'sport_association_id': self.source_association, 'user_id': self.source_owner, 'medical_id': medical}],
        }, {f'files/medical_certificates/{document}/medical.pdf': b'certificate bytes'}))
        self.assertEqual(op.preview['missing_relations'], 1)
        status = self.client.get(f'{self.endpoint}/{op.id}/status')
        self.assertNotIn('missing_relation_details', status.data['preview'])
        self.start(op)
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        self.assertFalse(op.upload_path)
        certificate = MedicalCertificate.objects.get(pk=medical)
        self.assertIsNone(certificate.user_id)
        self.assertEqual(str(certificate.document_id), document)
        self.assertEqual(str(Subscription.objects.get(pk=subscription).medical_id), medical)
        with default_storage.open(Document.objects.get(pk=document).filepath, 'rb') as source:
            self.assertEqual(source.read(), b'certificate bytes')
        report = self.client.get(f'{self.endpoint}/{op.id}/missing-relations')
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report['Cache-Control'], 'no-store')
        self.assertEqual(json.loads(report.content)['missing_relations'], [{
            'model': 'MedicalCertificate', 'record_id': medical, 'field': 'user_id',
            'target_model': 'User', 'target_id': missing_user,
        }])
        self.client.force_authenticate(User.objects.get(pk=missing_user))
        self.assertIn(self.client.get(f'{self.endpoint}/{op.id}/missing-relations').status_code, (403, 404))

    def test_missing_recovery_relationship_blocks_replacement(self):
        old = Associate.objects.create(sport_association=self.association, first_name='Original')
        op = self.start(self.upload())
        validate = Archive.validate

        def validate_with_incomplete_recovery(archive):
            result = validate(archive)
            if archive.association.get('denomination') == 'Current ASD':
                archive.missing_relations = [{'model': 'MedicalCertificate', 'field': 'user_id'}]
            return result

        with patch.object(Archive, 'validate', validate_with_incomplete_recovery):
            execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        self.assertIn('relazioni mancanti', op.error)
        self.assertTrue(Associate.objects.filter(pk=old.pk).exists())

    def test_dismiss_terminal_result_preserves_report_and_backup(self):
        op = self.upload()
        response = self.client.post(f'{self.endpoint}/{op.id}/dismiss', {}, format='json')
        self.assertEqual(response.status_code, 400)
        DataRestore.objects.filter(pk=op.pk).update(state='failed', stage='failed',
                                                  error='Previous failure', backup_path='retained/recovery.zip')
        with patch('instance.restore.views.restore_instance_data.apply_async') as dispatch:
            response = self.client.post(f'{self.endpoint}/{op.id}/dismiss', {}, format='json')
        self.assertEqual(response.status_code, 200)
        dispatch.assert_not_called()
        op.refresh_from_db()
        self.assertTrue(op.preview['dismissed'])
        self.assertEqual(op.state, 'failed')
        self.assertEqual(op.backup_path, 'retained/recovery.zip')
        self.assertEqual(op.error, 'Previous failure')
        self.assertEqual(self.client.get(f'{self.endpoint}/{op.id}/missing-relations').status_code, 200)

    def test_concurrency_maintenance_reload_and_resume(self):
        op = self.start(self.upload())
        response = self.client.get(self.endpoint)
        self.assertEqual(response.data['active']['id'], str(op.id))
        response = self.client.post('/association/export/start', {}, format='json')
        self.assertEqual(response.status_code, 503)
        with operation_lock(exclusive=True):
            with self.assertRaises(Busy):
                execute(op.pk)
            response = self.client.post(f'{self.endpoint}/{op.id}/resume', {}, format='json')
            self.assertEqual(response.status_code, 409)
        with patch('instance.restore.views.restore_instance_data.apply_async') as dispatch:
            response = self.client.post(f'{self.endpoint}/{op.id}/resume', {}, format='json')
            self.assertEqual(response.status_code, 202)
            self.assertEqual(dispatch.call_args.kwargs['task_id'], str(op.id))

    def test_interrupted_attempt_retries_and_cleans_uncommitted_media(self):
        op = self.start(self.upload())
        DataRestore.objects.filter(pk=op.pk).update(state='running', stage='restoring', attempts=1)
        orphan = default_storage.save(f'restores/{op.id}/media/orphan/file', ContentFile(b'partial'))
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        self.assertEqual(op.attempts, 2)
        self.assertFalse(default_storage.exists(orphan))

    def test_media_is_staged_and_existing_objects_never_overwritten(self):
        from docmanager.models import Document
        from application.models.user_models import SportAssociationDocumentsArchive
        identity = uuid.uuid4()
        old_key = default_storage.save('current/original.pdf', ContentFile(b'old bytes'))
        old_doc = Document.objects.create(document_id=identity, filename='original.pdf', filepath=old_key)
        SportAssociationDocumentsArchive.objects.create(sport_association=self.association, document=old_doc)
        op = self.start(self.upload(self.archive(
            {'Document': [{'document_id': str(identity), 'filename': 'new.pdf', 'filepath': 'bakney/source.pdf'}]},
            {f'files/documents/{identity}/new.pdf': b'new bytes'})))
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        doc = Document.objects.get(pk=identity)
        self.assertTrue(doc.filepath.startswith(f'restores/{op.id}/media/'))
        with default_storage.open(doc.filepath) as source:
            self.assertEqual(source.read(), b'new bytes')
        with default_storage.open(old_key) as source:
            self.assertEqual(source.read(), b'old bytes')

    def test_storage_failure_aborts_before_any_database_replacement(self):
        identity = str(uuid.uuid4())
        op = self.start(self.upload(self.archive(
            {'Document': [{'document_id': identity, 'filename': 'new.pdf'}]},
            {f'files/documents/{identity}/new.pdf': b'new bytes'})))
        actual_save = default_storage.save
        def fail_media(name, content, *args, **kwargs):
            if '/media/' in name:
                raise OSError('fixture disk full')
            return actual_save(name, content, *args, **kwargs)
        with patch.object(default_storage, 'save', side_effect=fail_media):
            execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        self.association.refresh_from_db()
        self.assertEqual(self.association.denomination, 'Current ASD')
        self.assertTrue(default_storage.exists(op.backup_path))

    def test_legacy_backslash_filename_stages_safely_and_survives_recovery(self):
        from docmanager.models import Document
        identity = str(uuid.uuid4())
        filename = r'Legacy registration\.pdf'
        content = self.archive({
            'Document': [{'document_id': identity, 'filename': filename, 'filepath': 'bakney/source.pdf'}],
            'SportAssociationDocumentsArchive': [{
                'sport_association_documents_archive_id': str(uuid.uuid4()),
                'sport_association_id': self.source_association, 'document_id': identity,
            }],
        }, {f'files/general_documents/{identity}/{filename}': b'original PDF bytes'})
        op = self.start(self.upload(content))
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        document = Document.objects.get(pk=identity)
        self.assertEqual(document.filename, filename)
        self.assertEqual(document.filepath.rsplit('/', 1)[-1], 'Legacy registration_.pdf')
        self.assertNotIn('\\', document.filepath)
        with default_storage.open(document.filepath, 'rb') as source:
            self.assertEqual(source.read(), b'original PDF bytes')
        replacement = self.start(self.upload())
        execute(replacement.pk)
        replacement.refresh_from_db()
        self.assertEqual(replacement.state, 'completed', replacement.error)
        with default_storage.open(replacement.backup_path, 'rb') as source:
            recovery = self.start(self.upload(source.read()))
        execute(recovery.pk)
        recovery.refresh_from_db()
        self.assertEqual(recovery.state, 'completed', recovery.error)
        document = Document.objects.get(pk=identity)
        self.assertEqual(document.filename, filename)
        self.assertNotIn('\\', document.filepath)
        with default_storage.open(document.filepath, 'rb') as source:
            self.assertEqual(source.read(), b'original PDF bytes')

    def test_process_interruption_rolls_back_then_resume_completes(self):
        old = Associate.objects.create(sport_association=self.association, first_name='Original')
        op = self.start(self.upload())
        with patch('instance.restore.replacement.ReplacementImporter._import_sport_association', side_effect=SystemExit('worker killed')):
            with self.assertRaises(SystemExit):
                execute(op.pk)
        self.assertTrue(Associate.objects.filter(pk=old.pk).exists())
        op.refresh_from_db()
        self.assertEqual(op.state, 'running')
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)

    def test_missing_confirmation_cancellation_expiration_and_upload_limit(self):
        from datetime import timedelta
        from django.utils import timezone
        op = self.upload()
        response = self.client.post(f'{self.endpoint}/{op.id}/start', {}, format='json')
        self.assertEqual(response.status_code, 400)
        DataRestore.objects.filter(pk=op.pk).update(created_at=timezone.now() - timedelta(days=2))
        response = self.client.post(f'{self.endpoint}/{op.id}/start', {'confirmation': 'RIPRISTINA'}, format='json')
        self.assertEqual(response.status_code, 400)
        key = op.upload_path
        response = self.client.post(f'{self.endpoint}/{op.id}/cancel', {}, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertFalse(default_storage.exists(key))
        with override_settings(DATA_RESTORE_MAX_UPLOAD_BYTES=10):
            response = self.client.post(self.endpoint, {'file': SimpleUploadedFile('big.zip', self.archive())}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_upload_limit_accepts_five_gib_and_rejects_the_next_byte(self):
        from django.core.files.uploadhandler import StopUpload
        from instance.restore.locking import LimitedRestoreUploadHandler
        limit = 5 * 1024**3
        self.assertEqual(settings.DATA_RESTORE_MAX_UPLOAD_BYTES, limit)
        self.assertEqual(self.client.get(self.endpoint).data['max_upload_bytes'], limit)
        # Exercise the streaming boundary without allocating a multi-GB fixture.
        handler = LimitedRestoreUploadHandler(None)
        handler.received = limit - 1
        self.assertEqual(handler.receive_data_chunk(b'x', limit - 1), b'x')
        with self.assertRaises(StopUpload):
            handler.receive_data_chunk(b'x', limit)
        self.assertGreater(settings.DATA_RESTORE_MAX_EXPANDED_BYTES, limit)

    def test_non_owner_links_and_shared_account_credentials_are_preserved(self):
        old_collaborator = User.objects.create_user(username='old-collaborator', connected_user=self.owner, role=User.COLLABORATOR)
        shared = User.objects.create_user(username='shared', password='shared-secret')
        before = User.original_objects.values().get(pk=shared.pk)
        users = [{'user_id': self.source_owner, 'username': 'source-owner'},
                 {'user_id': str(shared.pk), 'username': 'archive-shared', 'connected_user_id': self.source_owner},
                 {'user_id': str(uuid.uuid4()), 'username': 'new-collaborator', 'connected_user_id': self.source_owner, 'role': User.COLLABORATOR}]
        op = self.start(self.upload(self.archive({'User': users})))
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        self.assertEqual(User.original_objects.values().get(pk=shared.pk), before)
        old_collaborator.refresh_from_db()
        self.assertIsNone(old_collaborator.connected_user_id)
        self.assertEqual(User.objects.get(username='new-collaborator').connected_user_id, self.owner.pk)

    def test_unrelated_document_collision_fails_without_deleting_it(self):
        from docmanager.models import Document
        unrelated = Document.objects.create(filename='unrelated.txt')
        op = self.start(self.upload(self.archive({'Document': [{'document_id': str(unrelated.pk), 'filename': 'archive.txt'}]})))
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.filename, 'unrelated.txt')
        self.association.refresh_from_db()
        self.assertEqual(self.association.denomination, 'Current ASD')

    def test_recovery_zip_can_restore_previous_data_and_billing_survives(self):
        from application.models.billing_models import BillingSubscription
        billing = BillingSubscription.objects.create(user=self.owner)
        old = Associate.objects.create(sport_association=self.association, first_name='Old data', deleted=True)
        op = self.start(self.upload())
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        self.assertFalse(Associate._base_manager.filter(pk=old.pk).exists())
        with default_storage.open(op.backup_path) as source:
            recovery = source.read()
        rollback = self.start(self.upload(recovery))
        execute(rollback.pk)
        rollback.refresh_from_db()
        self.assertEqual(rollback.state, 'completed', rollback.error)
        restored = Associate._base_manager.get(pk=old.pk)
        self.assertEqual(restored.first_name, 'Old data')
        self.assertTrue(restored.deleted)
        self.assertTrue(BillingSubscription.objects.filter(pk=billing.pk, user=self.owner).exists())

    def test_recovery_preserves_payment_and_communication_fields(self):
        from application.models import Payment
        from communications.models import CommunicationConfiguration
        payment = Payment._base_manager.create(user=self.owner, sport_association=self.association,
                                               payment_intent_id='pi_local_recovery', amount='12.00')
        communication = CommunicationConfiguration.objects.create(
            sport_association=self.association, email_smtp_password='local-smtp-secret')
        original_payment = Payment._base_manager.values().get(pk=payment.pk)
        original_communication = CommunicationConfiguration.objects.values().get(pk=communication.pk)
        ordinary = AssociationExportService(self.association.pk)
        self.assertNotIn('payment_intent_id', ordinary.serialize_record(payment))
        op = self.start(self.upload())
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        with default_storage.open(op.backup_path) as source:
            recovery = self.start(self.upload(source.read()))
        execute(recovery.pk)
        recovery.refresh_from_db()
        self.assertEqual(recovery.state, 'completed', recovery.error)
        self.assertEqual(Payment._base_manager.values().get(pk=payment.pk), original_payment)
        self.assertEqual(CommunicationConfiguration.objects.values().get(pk=communication.pk), original_communication)

    def test_recovery_reconnects_only_collaborators_detached_by_restore(self):
        from instance.models import DataRestoreUserDetachment
        collaborator = User.objects.create_user(username='recover-collaborator', password='RetainedSecret',
                                                 connected_user=self.owner, role=User.COLLABORATOR)
        credentials = collaborator.password
        op = self.start(self.upload())
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        collaborator.refresh_from_db()
        self.assertIsNone(collaborator.connected_user_id)
        self.assertFalse(collaborator.is_active)
        self.assertTrue(DataRestoreUserDetachment.objects.filter(user=collaborator, owner=self.owner).exists())
        with default_storage.open(op.backup_path) as source:
            recovery = self.start(self.upload(source.read()))
        execute(recovery.pk)
        recovery.refresh_from_db()
        self.assertEqual(recovery.state, 'completed', recovery.error)
        collaborator.refresh_from_db()
        self.assertEqual(collaborator.connected_user_id, self.owner.pk)
        self.assertTrue(collaborator.is_active)
        self.assertEqual(collaborator.password, credentials)
        self.assertFalse(DataRestoreUserDetachment.objects.filter(user=collaborator).exists())

    def test_recovery_backups_remain_listed_and_paginated_after_cancelled_uploads(self):
        backups = []
        for index in range(12):
            backups.append(DataRestore.objects.create(owner=self.owner, association_id=self.association.pk,
                state='completed', sha256='fixture', backup_path=f'fixture/{index}.zip'))
        for index in range(15):
            DataRestore.objects.create(owner=self.owner, association_id=self.association.pk,
                                       state='cancelled', sha256='fixture')
        response = self.client.get(self.endpoint)
        self.assertEqual(len(response.data['history']), 10)
        self.assertTrue(all(op['state'] == 'cancelled' for op in response.data['history']))
        first_page = response.data['backups']
        self.assertEqual(len(first_page), 10)
        cursor = response.data['backups_next']
        response = self.client.get(f'{self.endpoint}/backups', {'before': cursor})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['backups_next'])
        self.assertEqual({op['id'] for op in first_page + response.data['backups']}, {str(op.id) for op in backups})
        self.client.force_authenticate(User.objects.create_user(username='not-owner'))
        self.assertEqual(self.client.get(f'{self.endpoint}/backups').status_code, 403)

    def test_legacy_signature_url_survives_import_and_recovery_round_trip(self):
        from application.models import Subscription
        identity = str(uuid.uuid4())
        legacy_url = 'https://bakney.example.test/signatures/original.png'
        content = self.archive({'Subscription': [{
            'subscription_id': identity, 'sport_association_id': self.source_association,
            'user_id': self.source_owner, 'signature_url': legacy_url,
        }]})
        op = self.upload(content)
        self.assertEqual(op.preview['missing_media'], 1)
        self.start(op, allow_missing_media=True)
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        signature = Subscription.objects.get(pk=identity)
        self.assertEqual(signature.signature_url, legacy_url)
        self.assertFalse(signature.signature_storage_key)

        replacement = self.start(self.upload())
        execute(replacement.pk)
        replacement.refresh_from_db()
        self.assertEqual(replacement.state, 'completed', replacement.error)
        self.assertFalse(Subscription.objects.filter(pk=identity).exists())
        with default_storage.open(replacement.backup_path) as source:
            recovery = self.upload(source.read())
        self.start(recovery, allow_missing_media=True)
        execute(recovery.pk)
        recovery.refresh_from_db()
        self.assertEqual(recovery.state, 'completed', recovery.error)
        signature = Subscription.objects.get(pk=identity)
        self.assertEqual(signature.signature_url, legacy_url)
        self.assertEqual(signature.user_id, self.owner.pk)
        self.assertFalse(signature.signature_storage_key)

    def test_bakney_platform_invoice_becomes_a_recoverable_archive_document(self):
        from application.models.user_models import SportAssociationDocumentsArchive
        invoice_id, document_id = str(uuid.uuid4()), str(uuid.uuid4())
        extra = {
            'Document': [{'document_id': document_id, 'filename': 'bakney-invoice.pdf', 'filepath': 'legacy/invoice.pdf'}],
            'SportAssociationInvoices': [{
                'sport_association_invoice_id': invoice_id,
                'sport_association_id': self.source_association,
                'document_id': document_id, 'invoice_date': '2024-03-01',
            }],
        }
        op = self.upload(self.archive(extra, {f'files/invoices/{document_id}/invoice.pdf': b'invoice fixture'}))
        self.assertEqual(op.preview['legacy_invoices'], 1)
        self.start(op)
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        link = SportAssociationDocumentsArchive.objects.get(pk=invoice_id)
        self.assertEqual(link.sport_association_id, self.association.pk)
        self.assertEqual(str(link.document_id), document_id)
        self.assertEqual(link.date.isoformat(), '2024-03-01')
        with default_storage.open(link.document.filepath) as source:
            self.assertEqual(source.read(), b'invoice fixture')

        replacement = self.start(self.upload())
        execute(replacement.pk)
        replacement.refresh_from_db()
        self.assertEqual(replacement.state, 'completed', replacement.error)
        self.assertFalse(SportAssociationDocumentsArchive.objects.filter(pk=invoice_id).exists())
        with default_storage.open(replacement.backup_path) as source:
            recovery = self.start(self.upload(source.read()))
        execute(recovery.pk)
        recovery.refresh_from_db()
        self.assertEqual(recovery.state, 'completed', recovery.error)
        restored = SportAssociationDocumentsArchive.objects.get(pk=invoice_id)
        with default_storage.open(restored.document.filepath) as source:
            self.assertEqual(source.read(), b'invoice fixture')

        extra['SportAssociationInvoices'][0]['document_id'] = str(uuid.uuid4())
        response = self.client.post(self.endpoint, {'file': SimpleUploadedFile('bad.zip', self.archive(extra))}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_external_inbound_relationship_prevents_deletion(self):
        family = Family.objects.create()
        Associate.objects.create(sport_association=self.association, family=family, first_name='Owned')
        outsider = Associate.objects.create(family=family, first_name='Unrelated')
        op = self.start(self.upload())
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        self.assertTrue(Family.objects.filter(pk=family.pk).exists())
        self.assertTrue(Associate.objects.filter(pk=outsider.pk).exists())

    def test_cleanup_expires_reviews_but_keeps_active_uploads_and_recovery(self):
        from datetime import timedelta
        from django.utils import timezone
        from instance.tasks import cleanup_data_restores
        old = self.upload()
        key = old.upload_path
        DataRestore.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
        cleanup_data_restores.run()
        old.refresh_from_db()
        self.assertEqual(old.state, 'cancelled')
        self.assertFalse(default_storage.exists(key))
        active = self.start(self.upload())
        cleanup_data_restores.run()
        active.refresh_from_db()
        self.assertEqual(active.state, 'queued')
        self.assertTrue(default_storage.exists(active.upload_path))

    def test_normal_tasks_defer_during_restore_and_exclusive_lifecycle_work(self):
        from celery.exceptions import Retry
        from instance.restore.task_base import CoordinatedTask
        class Job(CoordinatedTask):
            name = 'fixture.normal-task'
            def run(self):
                return 'executed'
            def retry(self, **kwargs):
                raise Retry('deferred')
        from core.celery import app
        Job.bind(app)
        self.assertEqual(Job()(), 'executed')
        with operation_lock(exclusive=True):
            with self.assertRaises(Retry):
                Job()()
        self.start(self.upload())
        with self.assertRaises(Retry):
            Job()()

    def test_maintenance_deferrals_do_not_exhaust_business_or_restore_retries(self):
        from celery.exceptions import Retry
        from core.celery import app
        from instance.restore.task_base import CoordinatedTask
        from instance.tasks import restore_instance_data
        class Job(CoordinatedTask):
            name = 'fixture.real-retry-budget'
            max_retries = 3
            def run(self):
                raise self.retry(countdown=1)
        Job.bind(app)
        job = Job()
        headers = {'assozeta_published_at': 1000}
        with operation_lock(exclusive=True):
            for task, args in ((job, ()), (restore_instance_data, ('fixture-id',))):
                # Simulate many deliveries after two genuine business failures.
                for attempt in range(12):
                    task.push_request(called_directly=False, is_eager=True, retries=2,
                                      id='fixture-command', args=args, kwargs={}, headers=headers)
                    try:
                        with self.assertRaises(Retry) as caught:
                            task(*args)
                        self.assertEqual(caught.exception.sig.options['retries'], 2)
                        self.assertEqual(caught.exception.sig.options['task_id'], 'fixture-command')
                        self.assertEqual(caught.exception.sig.options['headers'], headers)
                    finally:
                        task.pop_request()
        self.assertIsNone(restore_instance_data.max_retries)
        # Normal failures still increment their original budget and then stop.
        for retries in (2, 3):
            job.push_request(called_directly=False, is_eager=True, retries=retries,
                             id='fixture-command', args=(), kwargs={})
            try:
                if retries == 2:
                    with self.assertRaises(Retry) as caught:
                        job()
                    self.assertEqual(caught.exception.sig.options['retries'], 3)
                else:
                    with self.assertRaises(job.MaxRetriesExceededError):
                        job()
            finally:
                job.pop_request()

    def test_cancelled_agent_thread_keeps_its_lease_until_database_work_finishes(self):
        import asyncio
        import threading
        from asgiref.sync import async_to_sync
        from django.db import close_old_connections
        from application.agent.core import Agent
        started, finish, finished = threading.Event(), threading.Event(), threading.Event()
        thread_errors = []
        owner_id = self.owner.pk
        def synchronous_tool(**kwargs):
            try:
                started.set()
                if not finish.wait(timeout=10):
                    raise AssertionError('Tool release timed out')
                User.objects.filter(pk=owner_id).update(first_name='Thread completed')
                return {'saved': True}
            except Exception as exc:
                thread_errors.append(exc)
                raise
            finally:
                close_old_connections()
                finished.set()
        agent = Agent.__new__(Agent)
        agent.sport_association_id = str(self.association.pk)
        agent.user_id = str(owner_id)
        async def scenario():
            with patch('application.agent.core.TOOL_FUNCTIONS', {'save_report': synchronous_tool}):
                task = asyncio.create_task(agent._execute_tool('save_report', {}))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertFalse(finished.is_set())
                    with self.assertRaises(Busy):
                        with operation_lock(exclusive=True):
                            pass
                finally:
                    finish.set()
                    self.assertTrue(await asyncio.to_thread(finished.wait, 5))
        async_to_sync(scenario)()
        self.assertEqual(thread_errors, [])
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, 'Thread completed')
        with operation_lock(exclusive=True):
            pass

    def test_retry_limit_releases_maintenance_after_repeated_worker_losses(self):
        op = self.start(self.upload())
        DataRestore.objects.filter(pk=op.pk).update(state='running', attempts=3)
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        self.assertIn('interrotto', op.error)
        self.assertFalse(op.upload_path)

    def test_missing_recovery_file_blocks_replacement(self):
        from docmanager.models import Document
        from application.models.user_models import SportAssociationDocumentsArchive
        doc = Document.objects.create(filename='lost.pdf', filepath='missing/lost.pdf')
        SportAssociationDocumentsArchive.objects.create(sport_association=self.association, document=doc)
        op = self.start(self.upload())
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.association.refresh_from_db()
        self.assertEqual(self.association.denomination, 'Current ASD')

    def test_active_root_and_credentials_survive_an_archived_deleted_association(self):
        def mark_deleted(entries):
            rows = json.loads(entries['data/01_sport_association.json'])
            rows[0]['deleted'] = True
            entries['data/01_sport_association.json'] = json.dumps(rows)
        op = self.start(self.upload(self.archive(mutate=mark_deleted)))
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        self.assertTrue(SportAssociation.objects.filter(pk=self.association.pk).exists())
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('LocalSecret'))
        self.assertEqual(self.client.get(self.endpoint).status_code, 200)

    def test_chat_operations_hold_a_lease_and_refuse_restore_maintenance(self):
        from asgiref.sync import async_to_sync
        from instance.restore.locking import coordinated_async
        events = []
        class Consumer:
            async def send_json(self, event):
                events.append(event)
            @coordinated_async
            async def mutate(self):
                with self_test.assertRaises(Busy):
                    with operation_lock(exclusive=True):
                        pass
                events.append({'executed': True})
        self_test = self
        async_to_sync(Consumer().mutate)()
        self.assertEqual(events, [{'executed': True}])
        self.start(self.upload())
        events.clear()
        async_to_sync(Consumer().mutate)()
        self.assertEqual(events[0]['type'], 'error')
        self.assertNotIn('executed', events[0])

    def test_foreign_association_identity_is_mapped_to_local_root(self):
        self.source_association = str(uuid.uuid4())
        identity = str(uuid.uuid4())
        op = self.start(self.upload(self.archive({'Associate': [{
            'associate_id': identity, 'sport_association_id': self.source_association, 'first_name': 'Imported',
        }]})))
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        self.assertEqual(Associate.objects.get(pk=identity).sport_association_id, self.association.pk)
        self.assertFalse(SportAssociation.original_objects.filter(pk=self.source_association).exists())
        self.config.refresh_from_db()
        self.assertEqual(self.config.primary_association_id, self.association.pk)

    def test_external_many_to_many_reference_is_preserved(self):
        from docmanager.models import Document
        from application.models.user_models import SportAssociationDocumentsArchive, Instructor
        key = default_storage.save('current/shared.pdf', ContentFile(b'shared bytes'))
        doc = Document.objects.create(filename='shared.pdf', filepath=key)
        SportAssociationDocumentsArchive.objects.create(sport_association=self.association, document=doc)
        outsider = User.objects.create_user(username='outside-instructor')
        instructor = Instructor.objects.create(user=outsider, first_name='Outside', last_name='Graph')
        instructor.documents.add(doc)
        op = self.start(self.upload())
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'failed')
        self.assertIn('riferimenti esterni', op.error)
        self.assertTrue(instructor.documents.filter(pk=doc.pk).exists())
        self.assertTrue(default_storage.exists(key))

    def test_queued_business_commands_cannot_replay_after_restore(self):
        from core.celery import app
        from instance.restore.task_base import CoordinatedTask, StaleDataTask, stamp_published_task
        import time
        class Job(CoordinatedTask):
            name = 'fixture.stale-command'
            def run(self):
                return 'executed'
        Job.bind(app)
        old_headers = {}
        stamp_published_task(headers=old_headers)
        original = old_headers.copy()
        stamp_published_task(headers=old_headers)
        self.assertEqual(old_headers, original)
        op = self.start(self.upload())
        execute(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.state, 'completed', op.error)
        job = Job()
        job.push_request(called_directly=False, headers=old_headers)
        try:
            with self.assertRaises(StaleDataTask):
                job()
        finally:
            job.pop_request()
        job.push_request(called_directly=False, headers={'assozeta_published_at': time.time()})
        try:
            self.assertEqual(job(), 'executed')
        finally:
            job.pop_request()
