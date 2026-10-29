"""Historical message acceptance through real replacement and recovery orchestration.

Use full PostgreSQL migrations and the synthetic contract settings. These are
TransactionTestCase tests: execute() must own its transaction and advisory lock.
Only Celery dispatch is patched by the existing start helper; persistence,
deletion, audit/index hooks, recovery export, storage and locks remain real.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
from pathlib import Path
from uuid import UUID
import zipfile

from auditlog.models import LogEntry
from auditlog.signals import post_log
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, connection
from django.test import TransactionTestCase, override_settings

from application.audit_resolvers import setup_audit_resolvers
from application.models import Associate, Family, SportAssociation, User
from application.models.audit_models import AuditLogIndex
from application.models.billing_models import BillingPlan, BillingSubscription
from application.models.user_models import PreviewAndCustomFeatures, SportAssociationDocumentsArchive
from application.services.import_service import AssociationImportService
from communications.models import Message, MessageTransaction
from docmanager.models import Document
from instance.models import DataRestoreUserDetachment, InstanceConfiguration
from instance.restore.execution import execute
from instance.tests import test_data_restore as restore_helpers
from TESTS.fixtures import historical_message_mutations as fixture


@override_settings(AUDITLOG_STORE_JSON_CHANGES=False)
class HistoricalMessageReplacementRestoreTests(TransactionTestCase):
    def setUp(self):
        # Explicit helper reuse, without inheriting/recollecting RestoreTests.
        restore_helpers.RestoreTests.setUp(self)
        self.enterContext(self.settings(
            CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
            AWS_S3_PUBLIC_BASE_URL='',
        ))
        self.assertEqual(connection.vendor, 'postgresql', 'Parent must supply disposable PostgreSQL')
        setup_audit_resolvers()  # Real resolvers, after test-schema creation.
        self.contract = fixture.load_contract()
        self.source = fixture.derived_archive(self.contract)
        self.expected = fixture.expected_projection('imported', self.contract)
        self.historical_zip = fixture.archive_bytes(self.source)
        self.assertEqual(hashlib.sha256(self.historical_zip).hexdigest(), fixture.ARCHIVE_SHA256)
        self.assertNotEqual(str(self.association.pk), self.contract['ids']['association'])
        self.assertNotEqual(str(self.owner.pk), self.contract['ids']['owner'])

        self.old_family = Family.objects.create()
        self.old_associate = Associate.objects.create(
            sport_association=self.association, family=self.old_family, first_name='Before replacement',
        )
        self.old_message = Message.objects.create(
            sport_association=self.association, type=Message.EMAIL, message='Before replacement',
        )
        self.old_deliveries = [MessageTransaction.objects.create(
            message=self.old_message, recipient=f'old-{number}@example.test',
        ) for number in range(2)]
        # Same document identity as the incoming archive: replacement must delete
        # the old row and stage new bytes, without overwriting the old file.
        self.old_key = default_storage.save('current/historical.txt', ContentFile(b'old attachment\n'))
        self.old_document = Document.objects.create(
            document_id=self.contract['ids']['document'], filename='old.txt', filepath=self.old_key,
        )
        self.old_link = SportAssociationDocumentsArchive.objects.create(
            sport_association=self.association, document=self.old_document,
        )
        self.outsider = User.objects.create_user(username='unrelated-historical-restore')
        self.unrelated_key = default_storage.save('unrelated/keep.txt', ContentFile(b'keep unrelated\n'))
        self.unrelated_document = Document.objects.create(filename='keep.txt', filepath=self.unrelated_key)
        self.local_plan = BillingPlan.objects.create(name='Local installation plan')
        self.billing = BillingSubscription.objects.create(user=self.owner, billing_plan=self.local_plan)
        self.local_feature = PreviewAndCustomFeatures.objects.create(name='Local feature', description='Keep')
        self.owner.preview_and_custom_features.add(self.local_feature)
        self.retained = [
            (type(obj), self._manager(type(obj)).values().get(pk=obj.pk))
            for obj in (self.config, self.owner, self.outsider, self.unrelated_document,
                        self.local_plan, self.billing, self.local_feature)
        ]
        self.initial_logs = list(LogEntry.objects.order_by('pk').values())
        self.initial_indexes = list(AuditLogIndex.objects.order_by('pk').values())

    @staticmethod
    def _manager(model):
        return getattr(model, 'original_objects', model._base_manager)

    def archive(self, *args, **kwargs):
        return restore_helpers.RestoreTests.archive(self, *args, **kwargs)

    def upload(self, *args, **kwargs):
        return restore_helpers.RestoreTests.upload(self, *args, **kwargs)

    def start(self, *args, **kwargs):
        return restore_helpers.RestoreTests.start(self, *args, **kwargs)

    def _queue_historical(self, content=None):
        operation = self.upload(self.historical_zip if content is None else content)
        self.assertEqual(operation.preview['skipped_records'], {
            'BillingPlan': 1, 'PreviewAndCustomFeatures': 1,
        })
        self.assertEqual(operation.preview['records'], 16)
        self.assertEqual(operation.preview['files'], 1)
        self.assertEqual(operation.preview['missing_media'], 1)  # URL-only legacy signature.
        self.assertEqual(operation.preview['missing_relations'], 0)
        return self.start(operation, allow_missing_media=True)

    def _execute_success(self, operation):
        upload_key = operation.upload_path
        execute(operation.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'completed', operation.error)
        self.assertEqual(operation.stage, 'completed')
        self.assertEqual(operation.error, '')
        self.assertEqual(operation.attempts, 1)
        self.assertIsNotNone(operation.completed_at)
        self.assertEqual(operation.upload_path, '')
        self.assertFalse(default_storage.exists(upload_key))
        self.assertTrue(default_storage.exists(operation.backup_path))
        with default_storage.open(operation.backup_path, 'rb') as backup:
            self.assertEqual(hashlib.sha256(backup.read()).hexdigest(), operation.backup_sha256)

    def _assert_retained(self):
        for model, before in self.retained:
            self.assertEqual(self._manager(model).values().get(pk=before[model._meta.pk.attname]), before)
        self.assertEqual(set(self.owner.preview_and_custom_features.values_list('pk', flat=True)),
                         {self.local_feature.pk})
        self.assertEqual(SportAssociation.original_objects.count(), 1)
        self.assertEqual(set(User.original_objects.values_list('pk', flat=True)),
                         {self.owner.pk, self.outsider.pk})
        self.assertEqual(list(LogEntry.objects.filter(pk__in=[row['id'] for row in self.initial_logs])
                              .order_by('pk').values()), self.initial_logs)
        self.assertEqual(list(AuditLogIndex.objects.filter(pk__in=[row['audit_log_index_id']
                                                                  for row in self.initial_indexes])
                              .order_by('pk').values()), self.initial_indexes)
        self.assertFalse(User.original_objects.filter(pk=self.contract['ids']['owner']).exists())
        self.assertFalse(SportAssociation.original_objects.filter(pk=self.contract['ids']['association']).exists())
        self.assertFalse(BillingPlan.objects.filter(pk=self.source['entries'][
            'system/00_billing_plans.json'][0]['billing_plan_id']).exists())
        self.assertFalse(PreviewAndCustomFeatures.objects.filter(pk=self.source['entries'][
            'system/64_preview_features.json'][0]['preview_and_custom_features_id']).exists())
        for key, expected in ((self.old_key, b'old attachment\n'),
                              (self.unrelated_key, b'keep unrelated\n')):
            with default_storage.open(key, 'rb') as stored:
                self.assertEqual(stored.read(), expected)

    def _mapped_id(self, value):
        return {
            self.contract['ids']['association']: str(self.association.pk),
            self.contract['ids']['owner']: str(self.owner.pk),
        }.get(value, value)

    def _assert_historical_state(self, operation):
        self._assert_retained()
        self.assertFalse(Associate._base_manager.filter(pk=self.old_associate.pk).exists())
        self.assertFalse(Family.objects.filter(pk=self.old_family.pk).exists())
        self.assertFalse(Message.objects.filter(pk=self.old_message.pk).exists())
        self.assertFalse(MessageTransaction.objects.filter(pk__in=[d.pk for d in self.old_deliveries]).exists())
        self.assertFalse(SportAssociationDocumentsArchive.objects.filter(pk=self.old_link.pk).exists())

        # Complete exact child sets, with all ordinary fields and six source
        # timestamp fields. Never consult the obsolete import_window metadata.
        for model, group, pk_name in (
            (Message, 'messages', 'message_id'),
            (MessageTransaction, 'deliveries', 'message_transaction_id'),
        ):
            actual = {str(row.pk): row for row in model.objects.all()}
            self.assertEqual(set(actual), {row[pk_name] for row in self.expected[group]})
            for source in self.expected[group]:
                row = actual[source[pk_name]]
                for field, text in source.items():
                    with self.subTest(model=model.__name__, pk=str(row.pk), field=field):
                        if field in ('created_at', 'sent_on', 'delivered_on', 'opened_on', 'clicked_on', 'bounced_on'):
                            expected = None if text is None else datetime.fromisoformat(text)
                        elif field.endswith('_id'):
                            expected = UUID(self._mapped_id(text))
                        else:
                            expected = text
                        self.assertEqual(getattr(row, field), expected)
                if model is Message:
                    self.assertEqual(set(row.messagetransaction_set.values_list('pk', flat=True)), {
                        UUID(child['message_transaction_id']) for child in self.expected['deliveries']
                        if child['message_id'] == source['message_id']
                    })
                else:
                    self.assertEqual(row.message.sport_association_id, self.association.pk)

        # Preserve the rest of the immutable archive too. Source fields here are
        # ordinary fields, not unspecified automatic defaults on other models.
        classes = {model.__name__: model for _, model in AssociationImportService.IMPORT_ORDER}
        for spec in self.source['entries']['manifest.json']['models_exported']:
            name = spec['name']
            if name in ('User', 'PreviewAndCustomFeatures', 'BillingPlan', 'Message', 'MessageTransaction'):
                continue
            model = classes.get(name)
            if model is None:  # Empty retired SportAssociationInvoices section.
                self.assertEqual(self.source['entries'][spec['file']], [])
                continue
            rows = self.source['entries'][spec['file']]
            expected_ids = {UUID(self._mapped_id(source[model._meta.pk.name])) for source in rows}
            if model is Document:
                expected_ids.add(self.unrelated_document.pk)
            self.assertEqual(set(self._manager(model).values_list('pk', flat=True)), expected_ids, name)
            for source in rows:
                row = self._manager(model).get(pk=self._mapped_id(source[model._meta.pk.name]))
                for name, value in source.items():
                    if model is Document and name == 'filepath':
                        continue  # Staged path and original bytes asserted below.
                    field = next(field for field in model._meta.concrete_fields if field.attname == name)
                    if field.is_relation:
                        value = None if value is None else field.target_field.to_python(self._mapped_id(value))
                    else:
                        value = field.to_python(self._mapped_id(value) if name == model._meta.pk.name else value)
                    self.assertEqual(getattr(row, name), value, (model.__name__, name))
        for member, content in self.source['text_files'].items():
            document = Document.objects.get(pk=member.split('/')[2])
            self.assertTrue(document.filepath.startswith(f'restores/{operation.id}/media/'))
            self.assertEqual(operation.media[f'Document:{document.pk}'], document.filepath)
            with default_storage.open(document.filepath, 'rb') as stored:
                self.assertEqual(stored.read(), content.encode('utf-8'))
        self.assertEqual(len(operation.media), 1)

    def _assert_audit_delta(self, before, *, deleted_ids, historical=True):
        self.assertTrue(before <= self._log_ids(), 'Existing audit history must remain append-only')
        events = LogEntry.objects.get_for_model(Message).exclude(pk__in=before)
        creates = events.filter(action=LogEntry.Action.CREATE)
        deletes = events.filter(action=LogEntry.Action.DELETE)
        self.assertEqual(events.count(), (4 if historical else 0) + len(deleted_ids))
        self.assertCountEqual(deletes.values_list('object_pk', flat=True), deleted_ids)
        self.assertCountEqual(creates.values_list('object_pk', flat=True),
                              [row['message_id'] for row in self.expected['messages']] if historical else [])
        for source in self.expected['messages'] if historical else []:
            event = creates.get(object_pk=source['message_id'])
            self.assertIsNone(event.actor_id)  # execute is the worker boundary, outside the HTTP actor.
            self.assertEqual(event.changes['sport_association'], ['None', str(self.association.pk)])
            timestamp = source['created_at']
            serialized = 'None' if timestamp is None else str(
                datetime.fromisoformat(timestamp).astimezone(timezone.utc).replace(tzinfo=None),
            )
            self.assertEqual(event.changes['created_at'], ['None', serialized])
        for event in events:
            indexes = AuditLogIndex.objects.filter(log_entry=event)
            self.assertEqual(indexes.count(), 1)
            index = indexes.get()
            self.assertEqual(index.sport_association_id, self.association.pk)
            self.assertEqual(index.resolution_path, 'direct' if event.action == LogEntry.Action.CREATE
                             else 'changes_json.sport_association')
        self.assertFalse(LogEntry.objects.get_for_model(MessageTransaction).exists())

    def _log_ids(self):
        return set(LogEntry.objects.values_list('pk', flat=True))

    def _database_state(self):
        models = {model for _, model in AssociationImportService.IMPORT_ORDER}
        models.update((LogEntry, AuditLogIndex, InstanceConfiguration, BillingPlan,
                       BillingSubscription, PreviewAndCustomFeatures, DataRestoreUserDetachment))
        models.update(field.remote_field.through for model in tuple(models)
                      for field in model._meta.local_many_to_many)
        return {model._meta.label: list(self._manager(model).order_by(model._meta.pk.name).values())
                for model in models}

    def _files(self):
        root = Path(self.directory.name) / 'media'
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}

    def _recovery_bytes(self, operation, *, historical):
        with default_storage.open(operation.backup_path, 'rb') as stored:
            content = stored.read()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            messages = json.loads(archive.read(fixture.MESSAGE_SECTION))
            deliveries = json.loads(archive.read(fixture.DELIVERY_SECTION))
            if historical:
                # Independently compare exported fields to the immutable source;
                # the exporter's current output is never used as its own oracle.
                for actual, sources, pk in ((messages, self.expected['messages'], 'message_id'),
                                            (deliveries, self.expected['deliveries'], 'message_transaction_id')):
                    self.assertEqual({row[pk] for row in actual}, {row[pk] for row in sources})
                    for source in sources:
                        row = next(row for row in actual if row[pk] == source[pk])
                        for field, value in source.items():
                            if field in ('created_at', 'sent_on', 'delivered_on', 'opened_on', 'clicked_on', 'bounced_on'):
                                self.assertEqual(None if row[field] is None else datetime.fromisoformat(row[field]),
                                                 None if value is None else datetime.fromisoformat(value))
                            else:
                                self.assertEqual(row[field], self._mapped_id(value) if field.endswith('_id') else value)
                for member, text in self.source['text_files'].items():
                    document_id = member.split('/')[2]
                    matches = [name for name in archive.namelist() if name.startswith('files/')
                               and name.split('/')[2] == document_id]
                    self.assertEqual(len(matches), 1)
                    self.assertEqual(archive.read(matches[0]), text.encode('utf-8'))
            else:
                self.assertEqual([row['message_id'] for row in messages], [str(self.old_message.pk)])
                self.assertCountEqual([row['message_transaction_id'] for row in deliveries],
                                      [str(row.pk) for row in self.old_deliveries])
        return content

    def test_historical_replacement_preserves_exact_graph_source_instants_and_audits(self):
        before = self._log_ids()
        operation = self._queue_historical()
        self.assertEqual(operation.sha256, fixture.ARCHIVE_SHA256)
        self._execute_success(operation)
        self._assert_historical_state(operation)
        self._assert_audit_delta(before, deleted_ids=[str(self.old_message.pk)])
        self._recovery_bytes(operation, historical=False)

    def test_repeated_replacement_and_duplicate_delivery_preserve_complete_message_state(self):
        first = self._queue_historical()
        self._execute_success(first)
        self._assert_historical_state(first)
        before = self._log_ids()
        second = self._queue_historical()
        self._execute_success(second)
        self._assert_historical_state(second)
        self._assert_audit_delta(before, deleted_ids=[row['message_id'] for row in self.expected['messages']])
        self._recovery_bytes(second, historical=True)
        self.assertNotEqual(first.backup_path, second.backup_path)
        self.assertNotEqual(first.media, second.media)
        for key in first.media.values():
            with default_storage.open(key, 'rb') as stored:
                self.assertEqual(stored.read(), b'Synthetic historical attachment.\n')
        before_db, before_files = self._database_state(), self._files()
        receipt = type(second).objects.values().get(pk=second.pk)
        execute(second.pk)
        self.assertEqual(self._database_state(), before_db)
        self.assertEqual(self._files(), before_files)
        self.assertEqual(type(second).objects.values().get(pk=second.pk), receipt)

    def test_recovery_export_reimport_restores_complete_historical_messages_and_media(self):
        initial = self._queue_historical()
        self._execute_success(initial)
        self._assert_historical_state(initial)
        # Reuse the existing complete synthetic empty archive; do not trim or
        # mutate the immutable historical archive to remove messages.
        before = self._log_ids()
        cleared = self.start(self.upload(self.archive()))
        self._execute_success(cleared)
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(MessageTransaction.objects.count(), 0)
        self.assertFalse(Document.objects.filter(pk=self.contract['ids']['document']).exists())
        for key in initial.media.values():
            with default_storage.open(key, 'rb') as stored:
                self.assertEqual(stored.read(), b'Synthetic historical attachment.\n')
        self._assert_audit_delta(before, deleted_ids=[row['message_id'] for row in self.expected['messages']],
                                 historical=False)
        recovery_bytes = self._recovery_bytes(cleared, historical=True)
        before = self._log_ids()
        recovery = self.upload(recovery_bytes)
        self.assertEqual(recovery.preview['missing_relations'], 0)
        self.assertEqual(recovery.preview['missing_media'], 1)
        self.assertEqual(recovery.preview['files'], 1)
        self.start(recovery, allow_missing_media=True)
        self._execute_success(recovery)
        self._assert_historical_state(recovery)
        self._assert_audit_delta(before, deleted_ids=[])

    def _assert_failed_rollback(self, operation, before_db, before_files, upload_key):
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'failed')
        self.assertEqual(operation.stage, 'failed')
        self.assertEqual(operation.attempts, 1)
        self.assertIsNone(operation.completed_at)
        self.assertTrue(operation.error)
        self.assertEqual(self._database_state(), before_db)
        self.assertEqual(operation.media, {})
        self.assertEqual(operation.upload_path, '')
        self.assertFalse(default_storage.exists(upload_key))
        # Only the new durable safety backup may remain after failed staging.
        self.assertEqual(self._files(), {**before_files, operation.backup_path: self._recovery_bytes(
            operation, historical=False,
        )})
        with default_storage.open(operation.backup_path, 'rb') as backup:
            self.assertEqual(hashlib.sha256(backup.read()).hexdigest(), operation.backup_sha256)

    def test_audit_failure_after_real_deletion_insert_and_index_rolls_back_and_cleans_media(self):
        before_db, before_files = self._database_state(), self._files()
        operation = self._queue_historical()
        upload_key = operation.upload_path
        observed = []

        def fail_after_index(sender, instance, action, log_created, log_entry, **kwargs):
            if action != LogEntry.Action.CREATE:
                return
            self.assertTrue(connection.in_atomic_block)
            self.assertTrue(log_created)
            self.assertFalse(Message.objects.filter(pk=self.old_message.pk).exists())
            self.assertFalse(MessageTransaction.objects.filter(pk__in=[row.pk for row in self.old_deliveries]).exists())
            self.assertTrue(Message.objects.filter(pk=instance.pk).exists())
            self.assertEqual(log_entry.sport_association_index.sport_association_id, self.association.pk)
            # Documents import before messages; this same-PK row must already
            # contain the incoming metadata, not the deleted local document.
            replaced_document = Document.objects.get(pk=self.old_document.pk)
            self.assertEqual(replaced_document.filename, 'historical.txt')
            self.assertFalse(SportAssociationDocumentsArchive.objects.filter(pk=self.old_link.pk).exists())
            self.assertTrue(any(f'restores/{operation.id}/media/' in key for key in self._files()))
            observed.append(log_entry.pk)
            raise RuntimeError('synthetic restore audit hook failure after real index')

        post_log.connect(fail_after_index, sender=Message, weak=False)
        try:
            execute(operation.pk)
        finally:
            post_log.disconnect(fail_after_index, sender=Message)
        self.assertEqual(len(observed), 1, 'Must reach real replacement mutation/index, not fail during setup')
        self._assert_failed_rollback(operation, before_db, before_files, upload_key)

    def test_null_last_delivery_sent_on_hits_postgresql_constraint_and_rolls_back_complete_restore(self):
        # Separate temporary variant; never modify/re-sign the fixed specimen.
        with zipfile.ZipFile(io.BytesIO(self.historical_zip)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        rows = json.loads(entries[fixture.DELIVERY_SECTION])
        rows[-1]['sent_on'] = None
        entries[fixture.DELIVERY_SECTION] = json.dumps(rows).encode('utf-8')
        variant = io.BytesIO()
        with zipfile.ZipFile(variant, 'w') as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        before_db, before_files = self._database_state(), self._files()
        operation = self._queue_historical(variant.getvalue())
        upload_key = operation.upload_path
        failures = []

        class CaptureFailure(logging.Handler):
            def emit(self, record):
                if record.exc_info:
                    failures.append(record.exc_info[1])

        # Observe the real exception swallowed by execute's failure receipt;
        # no persistence/error path is patched or replaced.
        handler = CaptureFailure()
        logger = logging.getLogger('instance.restore.execution')
        logger.addHandler(handler)
        try:
            execute(operation.pk)
        finally:
            logger.removeHandler(handler)
        self.assertEqual(len(failures), 1, 'Require the intended constraint failure, not setup failure')
        self.assertIsInstance(failures[0], IntegrityError)
        diagnostic = failures[0].__cause__.diag
        self.assertEqual(diagnostic.sqlstate, '23502')
        self.assertEqual(diagnostic.table_name, MessageTransaction._meta.db_table)
        self.assertEqual(diagnostic.column_name, 'sent_on')
        self._assert_failed_rollback(operation, before_db, before_files, upload_key)
