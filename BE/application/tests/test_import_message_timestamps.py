"""Historical timestamps must survive the supported, real-ORM archive import.

Run with the repository root and BE on PYTHONPATH and full PostgreSQL migrations;
see docs/architecture/go-migration/message_import_timestamps.md. No exporter,
field/save patches, clock patches, or post-import timestamp repairs are used.
"""
from datetime import datetime, timezone as datetime_timezone
import json
from pathlib import Path
import tempfile
from uuid import UUID
import zipfile

from auditlog.context import disable_auditlog, set_actor
from auditlog.models import LogEntry
from auditlog.signals import post_log, pre_log
from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.db.models.signals import post_save, pre_save
from django.test import TestCase, override_settings
from django.utils import timezone

from application.audit_resolvers import setup_audit_resolvers
from application.models import SportAssociation, User
from application.models.audit_models import AuditLogIndex
from application.services.import_service import AssociationImportService, ImportOptions
from communications.models import Message, MessageTransaction
from docmanager.models import Document
from TESTS.fixtures import historical_message_mutations as fixture


class HistoricalMessageTimestampImportTests(TestCase):
    def setUp(self):
        # Defer all fixture reads, storage configuration and imports into the
        # test lifecycle; importing this module does not install any app rows.
        self.contract = fixture.load_contract()
        self.expected = fixture.expected_projection('imported', self.contract)
        self.archive = fixture.derived_archive(self.contract)
        workdir = tempfile.TemporaryDirectory()
        self.addCleanup(workdir.cleanup)
        self.enterContext(self.settings(
            STORAGES={
                'default': {
                    'BACKEND': 'django.core.files.storage.FileSystemStorage',
                    'OPTIONS': {'location': workdir.name},
                },
                'staticfiles': {
                    'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
                },
            },
            AWS_S3_PUBLIC_BASE_URL='',
        ))
        archive_path = Path(workdir.name) / 'historical.zip'
        archive_path.write_bytes(fixture.archive_bytes(self.archive))
        self.service = AssociationImportService(
            str(archive_path), ImportOptions(owner_password='SyntheticRecovery!123'),
        )
        validation = self.service.validate()
        self.assertTrue(validation.is_valid, validation.errors)
        self.assertEqual(validation.errors, [])
        self.assertEqual(validation.warnings, [])
        self.association = self.service.import_all()

        # Every preservation test must first prove the full import succeeded.
        # Setup/dependency/constraint failures are errors, never expected RED.
        self.assertEqual(self.service.errors, [])
        self.assertEqual(self.service.skipped_records, {
            'BillingPlan': 1, 'PreviewAndCustomFeatures': 1,
        })
        self.assertEqual(self.service.stats['Message'], 4)
        self.assertEqual(self.service.stats['MessageTransaction'], 5)
        self.assertEqual(self.service.stats['files_imported'], 1)
        self.assertEqual(self.service.stats['deferred_fks_failed'], 0)
        self.assertEqual(self.service.stats['m2m_failed'], 0)
        self.association.refresh_from_db()
        self.assertEqual(self.association.pk, UUID(self.contract['ids']['association']))
        self.assertEqual(self.association.user_id, UUID(self.contract['ids']['owner']))
        self.assertTrue(SportAssociation.original_objects.filter(pk=self.association.pk).exists())
        self.assertTrue(User.original_objects.filter(pk=self.association.user_id).exists())

        # Fetch persisted rows anew, never the importer's in-memory objects.
        self.messages = {str(row.pk): row for row in Message.objects.all()}
        self.deliveries = {str(row.pk): row for row in MessageTransaction.objects.all()}
        self.assertEqual(set(self.messages), {
            row['message_id'] for row in self.expected['messages']
        })
        self.assertEqual(set(self.deliveries), {
            row['message_transaction_id'] for row in self.expected['deliveries']
        })
        for source in self.expected['messages']:
            row = self.messages[source['message_id']]
            self.assertEqual(row.pk, UUID(source['message_id']))
            self.assertEqual(row.sport_association_id, UUID(source['sport_association_id']))
            self.assertEqual(row.sport_association_id, self.association.pk)
            for field in ('type', 'message', 'subject'):
                self.assertEqual(getattr(row, field), source[field], (str(row.pk), field))
            self.assertEqual(
                set(row.messagetransaction_set.values_list('pk', flat=True)),
                {UUID(child['message_transaction_id']) for child in self.expected['deliveries']
                 if child['message_id'] == source['message_id']},
            )
        for source in self.expected['deliveries']:
            row = self.deliveries[source['message_transaction_id']]
            self.assertEqual(row.pk, UUID(source['message_transaction_id']))
            self.assertEqual(row.message_id, UUID(source['message_id']))
            self.assertEqual(row.message.sport_association_id, self.association.pk)
            for field in ('recipient', 'bounced_reason', 'bounced_type', 'bounced_status'):
                self.assertEqual(getattr(row, field), source[field], (str(row.pk), field))

        # Retain the real attachment path, rather than trimming the legacy ZIP
        # to two sections or using skip_files to bypass the supported path.
        for member, content in self.archive['text_files'].items():
            document_id = UUID(member.split('/')[2])
            document = Document.objects.get(pk=document_id)
            with default_storage.open(document.filepath, 'rb') as imported_file:
                self.assertEqual(imported_file.read(), content.encode('utf-8'))

    def test_full_historical_archive_import_succeeds_with_exact_rows_and_skips(self):
        # setUp verifies success, attachment bytes, UUID/FK membership and all
        # ordinary fields, including legacy SMS, HTML, whitespace, null/empty.
        self.assertEqual(len(self.messages), 4)
        self.assertEqual(len(self.deliveries), 5)

    def test_message_created_at_preserves_each_non_null_source_timestamp(self):
        for source in self.expected['messages']:
            if source['created_at'] is not None:
                with self.subTest(message_id=source['message_id']):
                    self.assertEqual(
                        self.messages[source['message_id']].created_at,
                        datetime.fromisoformat(source['created_at']),
                        'Import must preserve Message.created_at exactly',
                    )

    def test_message_created_at_preserves_explicit_source_null(self):
        source = next(row for row in self.expected['messages']
                      if row['message_id'] == self.contract['ids']['retained_post'])
        self.assertIsNone(source['created_at'])
        self.assertIsNone(
            self.messages[source['message_id']].created_at,
            'An explicit historical null must not become the import time',
        )

    def test_delivery_sent_on_preserves_each_source_timestamp(self):
        for source in self.expected['deliveries']:
            with self.subTest(message_transaction_id=source['message_transaction_id']):
                self.assertEqual(
                    self.deliveries[source['message_transaction_id']].sent_on,
                    datetime.fromisoformat(source['sent_on']),
                    'Import must preserve MessageTransaction.sent_on exactly',
                )

    def test_other_delivery_timestamps_preserve_exact_source_values_and_nulls(self):
        for source in self.expected['deliveries']:
            row = self.deliveries[source['message_transaction_id']]
            for field in ('delivered_on', 'opened_on', 'clicked_on', 'bounced_on'):
                with self.subTest(message_transaction_id=str(row.pk), field=field):
                    expected = (None if source[field] is None
                                else datetime.fromisoformat(source[field]))
                    self.assertEqual(getattr(row, field), expected)


@override_settings(AUDITLOG_STORE_JSON_CHANGES=False)
class MessageTimestampImportBoundaryTests(TestCase):
    """Extra cases use separate, in-memory variants; the five fixed tests stay intact."""

    def setUp(self):
        # Django initializes apps before creating the test database. Register
        # against the migrated test content types, as normal runtime startup does.
        from application.audit_resolvers import setup_audit_resolvers
        setup_audit_resolvers()
        # App startup may precede creation of the test schema. Register through
        # the real setup path now, as the existing audit regression suite does.
        setup_audit_resolvers()
        self.contract = fixture.load_contract()
        self.expected = fixture.expected_projection('imported', self.contract)
        workdir = tempfile.TemporaryDirectory()
        self.addCleanup(workdir.cleanup)
        self.media_root = Path(workdir.name) / 'media'
        self.enterContext(self.settings(
            STORAGES={
                'default': {
                    'BACKEND': 'django.core.files.storage.FileSystemStorage',
                    'OPTIONS': {'location': str(self.media_root)},
                },
                'staticfiles': {
                    'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
                },
            },
            AWS_S3_PUBLIC_BASE_URL='',
        ))
        self.archive_path = Path(workdir.name) / 'historical.zip'
        self.archive_path.write_bytes(fixture.archive_bytes())
        self.service = AssociationImportService(
            str(self.archive_path), ImportOptions(owner_password='SyntheticRecovery!123'),
        )

    def _edit_section(self, section, edit):
        # Only extra edge-case tests edit a temporary ZIP. Never feed altered
        # data back into the fixed fixture helper or its preservation oracle.
        with zipfile.ZipFile(self.archive_path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        rows = json.loads(entries[section])
        edit(rows)
        entries[section] = json.dumps(rows).encode('utf-8')
        with zipfile.ZipFile(self.archive_path, 'w') as archive:
            for name, content in entries.items():
                archive.writestr(name, content)

    def _import(self):
        validation = self.service.validate()
        self.assertEqual(validation.errors, [])
        self.assertEqual(validation.warnings, [])
        self.assertTrue(validation.is_valid)
        association = self.service.import_all()
        self.assertEqual(self.service.errors, [])
        self.assertEqual(self.service.stats['Message'], 4)
        self.assertEqual(self.service.stats['MessageTransaction'], 5)
        self.assertEqual(self.service.stats['files_imported'], 1)
        self.assertEqual(self.service.stats['deferred_fks_failed'], 0)
        self.assertEqual(self.service.stats['m2m_failed'], 0)
        self.assertEqual(self.service.skipped_records, {
            'BillingPlan': 1, 'PreviewAndCustomFeatures': 1,
        })
        return association

    def _assert_message_audits(self, association, actor_id=None, excluded_ids=()):
        messages = Message.objects.filter(sport_association=association)
        logs = LogEntry.objects.get_for_model(Message).filter(
            object_pk__in=[str(pk) for pk in messages.values_list('pk', flat=True)],
        )
        self.assertEqual(logs.count(), 4 - len(excluded_ids))
        for source in self.expected['messages']:
            with self.subTest(audit_message_id=source['message_id']):
                events = logs.filter(object_pk=source['message_id'])
                if source['message_id'] in excluded_ids:
                    self.assertFalse(events.exists())
                    continue
                event = events.get()  # Exactly one CREATE, no repair UPDATE.
                self.assertEqual(event.action, LogEntry.Action.CREATE)
                self.assertEqual(event.actor_id, actor_id)
                self.assertEqual(event.changes['sport_association'], [
                    'None', str(association.pk),
                ])
                if source['created_at'] is None:
                    # auditlog's default string changes encode a null datetime
                    # as this pair. In particular, no import-time value appears.
                    self.assertEqual(event.changes['created_at'], ['None', 'None'])
                else:
                    self.assertEqual(event.changes['created_at'][0], 'None')
                    expected = datetime.fromisoformat(source['created_at'])
                    self.assertEqual(
                        datetime.fromisoformat(event.changes['created_at'][1]),
                        expected.astimezone(datetime_timezone.utc).replace(tzinfo=None),
                    )
                indexes = AuditLogIndex.objects.filter(log_entry=event)
                self.assertEqual(indexes.count(), 1)
                index = indexes.get()
                self.assertEqual(index.sport_association_id, association.pk)
                self.assertEqual(index.resolution_path, 'direct')
        self.assertFalse(LogEntry.objects.get_for_model(MessageTransaction).exists())

    def _database_state(self):
        # Include every imported table plus audit/index rows, not just messages.
        model_classes = {model for _, model in self.service.IMPORT_ORDER}
        model_classes.update((LogEntry, AuditLogIndex))
        return {
            model: list(self.service._get_unfiltered_manager(model)
                        .order_by(model._meta.pk.name).values())
            for model in model_classes
        }

    def _assert_rollback(self, before):
        self.assertEqual(self._database_state(), before)
        self.assertTrue(self.service.errors)
        self.assertFalse(self.media_root.exists())

    def test_omitted_automatic_fields_use_defaults_while_present_peers_stay_exact(self):
        message_id = self.contract['ids']['retained_email']
        delivery_id = self.contract['ids']['retained_delivery']

        def omit_message_time(rows):
            next(row for row in rows if row['message_id'] == message_id).pop('created_at')

        def omit_delivery_time(rows):
            next(row for row in rows if row['message_transaction_id'] == delivery_id).pop('sent_on')

        self._edit_section(fixture.MESSAGE_SECTION, omit_message_time)
        self._edit_section(fixture.DELIVERY_SECTION, omit_delivery_time)
        observed = []

        def observe(sender, instance, raw, created, **kwargs):
            observed.append((sender, str(instance.pk), raw, created))

        for model in (Message, MessageTransaction):
            post_save.connect(observe, sender=model, weak=False)
            self.addCleanup(post_save.disconnect, observe, sender=model)
        self._import()
        self.assertEqual(len(observed), 9)
        for source in self.expected['messages']:
            row = Message.objects.get(pk=source['message_id'])
            if source['message_id'] == message_id:
                self.assertIsNotNone(row.created_at)
                self.assertTrue(timezone.is_aware(row.created_at))
                self.assertEqual(observed.count((Message, message_id, False, True)), 1)
                event = LogEntry.objects.get_for_object(row).get()
                self.assertEqual(event.action, LogEntry.Action.CREATE)
                self.assertEqual(
                    datetime.fromisoformat(event.changes['created_at'][1]),
                    row.created_at.astimezone(datetime_timezone.utc).replace(tzinfo=None),
                )
                self.assertEqual(event.sport_association_index.sport_association_id,
                                 row.sport_association_id)
            else:
                expected = (None if source['created_at'] is None
                            else datetime.fromisoformat(source['created_at']))
                self.assertEqual(row.created_at, expected)
        for source in self.expected['deliveries']:
            row = MessageTransaction.objects.get(pk=source['message_transaction_id'])
            if source['message_transaction_id'] == delivery_id:
                self.assertIsNotNone(row.sent_on)
                self.assertTrue(timezone.is_aware(row.sent_on))
                self.assertEqual(observed.count((MessageTransaction, delivery_id, False, True)), 1)
            else:
                self.assertEqual(row.sent_on, datetime.fromisoformat(source['sent_on']))

    def test_normal_application_creation_still_generates_current_timestamps(self):
        association = self._import()
        historical = datetime(2001, 2, 3, 4, 5, 6, 123456, tzinfo=datetime_timezone.utc)
        for supplied in ({}, {'created_at': historical}, {'created_at': None}):
            with self.subTest(supplied=supplied):
                before = timezone.now()
                message = Message.objects.create(
                    sport_association=association, type=Message.EMAIL,
                    message='Normal application creation', **supplied,
                )
                sent = {} if not supplied else {'sent_on': supplied['created_at']}
                delivery = MessageTransaction.objects.create(
                    message=message, recipient='normal@example.test', **sent,
                )
                after = timezone.now()
                message.refresh_from_db()
                delivery.refresh_from_db()
                # This range applies only to ordinary creation, never to an
                # explicitly imported timestamp or its preservation assertion.
                for value in (message.created_at, delivery.sent_on):
                    self.assertGreaterEqual(value, before)
                    self.assertLessEqual(value, after)

    @override_settings(AUDITLOG_DISABLE_ON_RAW_SAVE=True)
    def test_import_signals_and_exact_create_audits_keep_actor_and_tenant_index(self):
        actor = User.objects.create_user(
            username='synthetic-import-actor', email='import-actor@example.test',
        )
        observed = []
        logged = []

        def observe(sender, instance, raw, signal, **kwargs):
            value = instance.created_at if sender is Message else instance.sent_on
            observed.append((sender, str(instance.pk), signal, raw, value))

        def observe_log(sender, instance, action, log_created, log_entry, error, **kwargs):
            self.assertTrue(log_created)
            self.assertIsNone(error)
            self.assertEqual(action, LogEntry.Action.CREATE)
            logged.append(str(instance.pk))

        for model in (Message, MessageTransaction):
            for signal in (pre_save, post_save):
                signal.connect(observe, sender=model, weak=False)
                self.addCleanup(signal.disconnect, observe, sender=model)
        post_log.connect(observe_log, sender=Message, weak=False)
        self.addCleanup(post_log.disconnect, observe_log, sender=Message)
        with set_actor(actor):
            association = self._import()
        self._assert_message_audits(association, actor.pk)
        self.assertCountEqual(logged, [row['message_id'] for row in self.expected['messages']])
        self.assertEqual(len(observed), 18)
        for model, rows, pk_field, time_field in (
            (Message, self.expected['messages'], 'message_id', 'created_at'),
            (MessageTransaction, self.expected['deliveries'], 'message_transaction_id', 'sent_on'),
        ):
            for source in rows:
                value = (None if source[time_field] is None
                         else datetime.fromisoformat(source[time_field]))
                for signal in (pre_save, post_save):
                    self.assertEqual(observed.count((model, source[pk_field], signal, True, value)), 1)

    @override_settings(AUDITLOG_DISABLE_ON_RAW_SAVE=False)
    def test_raw_audit_setting_does_not_duplicate_import_create_events(self):
        self._assert_message_audits(self._import())

    def test_enclosing_audit_disable_is_respected_and_does_not_leak(self):
        before_logs = set(LogEntry.objects.values_list('pk', flat=True))
        before_indexes = set(AuditLogIndex.objects.values_list('pk', flat=True))
        with disable_auditlog():
            association = self._import()
        self.assertEqual(set(LogEntry.objects.values_list('pk', flat=True)), before_logs)
        self.assertEqual(set(AuditLogIndex.objects.values_list('pk', flat=True)), before_indexes)
        message = Message.objects.create(
            sport_association=association, type=Message.EMAIL, message='After audit context',
        )
        self.assertEqual(LogEntry.objects.get_for_object(message).get().action, LogEntry.Action.CREATE)

    def test_existing_audit_pre_log_veto_remains_effective(self):
        veto_id = self.contract['ids']['retained_email']

        def veto(sender, instance, **kwargs):
            if str(instance.pk) == veto_id:
                return False

        pre_log.connect(veto, sender=Message, weak=False)
        self.addCleanup(pre_log.disconnect, veto, sender=Message)
        association = self._import()
        self._assert_message_audits(association, excluded_ids=(veto_id,))

    def test_malformed_explicit_created_at_is_not_silently_converted_to_null(self):
        def corrupt_message_timestamp(rows):
            rows[-1]['created_at'] = 'not-an-iso-timestamp'

        self._edit_section(fixture.MESSAGE_SECTION, corrupt_message_timestamp)
        self.assertTrue(self.service.validate().is_valid)
        before = self._database_state()
        with self.assertRaisesRegex(ValueError, 'Invalid imported message timestamp'):
            self.service.import_all()
        self._assert_rollback(before)

    def test_unsupported_null_sent_on_rolls_back_entire_import_and_audits(self):
        def clear_last_sent_on(rows):
            rows[-1]['sent_on'] = None

        self._edit_section(fixture.DELIVERY_SECTION, clear_last_sent_on)
        self.assertTrue(self.service.validate().is_valid)
        before = self._database_state()
        with self.assertRaises(IntegrityError) as failure:
            self.service.import_all()
        # Require the intended PostgreSQL preservation failure, rather than
        # accepting an unrelated setup, FK or uniqueness failure as success.
        diagnostic = failure.exception.__cause__.diag
        self.assertEqual(diagnostic.sqlstate, '23502')
        self.assertEqual(diagnostic.table_name, MessageTransaction._meta.db_table)
        self.assertEqual(diagnostic.column_name, 'sent_on')
        self._assert_rollback(before)

    def test_audit_hook_failure_after_real_index_creation_rolls_back_import(self):
        self.assertTrue(self.service.validate().is_valid)
        before = self._database_state()
        observed = []

        def fail(sender, instance, log_created, log_entry, **kwargs):
            self.assertTrue(log_created)
            self.assertTrue(Message.objects.filter(pk=instance.pk).exists())
            self.assertEqual(log_entry.sport_association_index.sport_association_id,
                             instance.sport_association_id)
            observed.append(log_entry.pk)
            raise RuntimeError('synthetic message create audit failure')

        post_log.connect(fail, sender=Message, weak=False)
        self.addCleanup(post_log.disconnect, fail, sender=Message)
        with self.assertRaisesRegex(RuntimeError, 'synthetic message create audit failure'):
            self.service.import_all()
        self.assertEqual(len(observed), 1)
        self._assert_rollback(before)

    # Each pair is (archive text, independently authored UTC instant). These
    # variants belong only to temporary ZIPs; the immutable fixture stays intact.
    _UTC_FRACTIONAL_TIMESTAMPS = {
        'messages': {
            'retained_email': {'created_at': (
                '2001-01-01T00:00:00.123456Z', '2001-01-01T00:00:00.123456+00:00')},
            'retained_sms': {'created_at': (
                '2001-01-02T00:00:00.000001+00:00', '2001-01-02T00:00:00.000001+00:00')},
            'delete_email': {'created_at': (
                '2001-01-03T00:00:00.999999Z', '2001-01-03T00:00:00.999999+00:00')},
        },
        'deliveries': {
            'retained_delivery': {
                'sent_on': ('2001-01-01T00:00:01.234567Z', '2001-01-01T00:00:01.234567+00:00'),
                'delivered_on': ('2001-01-01T00:00:02.345678Z', '2001-01-01T00:00:02.345678+00:00'),
                'opened_on': ('2001-01-01T00:01:00.456789Z', '2001-01-01T00:01:00.456789+00:00'),
                'clicked_on': ('2001-01-01T00:01:01.567891Z', '2001-01-01T00:01:01.567891+00:00'),
            },
            'delete_bounced': {
                'sent_on': ('2001-01-03T00:00:01.678912Z', '2001-01-03T00:00:01.678912+00:00'),
                'bounced_on': ('2001-01-03T00:00:02.789123Z', '2001-01-03T00:00:02.789123+00:00'),
            },
        },
    }
    _OFFSET_FRACTIONAL_TIMESTAMPS = {
        'messages': {
            'retained_email': {'created_at': (
                '2001-01-01T00:00:00.123456+05:45', '2000-12-31T18:15:00.123456+00:00')},
            'retained_sms': {'created_at': (
                '2000-12-31T23:59:59.000001-03:30', '2001-01-01T03:29:59.000001+00:00')},
            'delete_email': {'created_at': (
                '2000-12-31T23:59:59.999999-03:30', '2001-01-01T03:29:59.999999+00:00')},
        },
        'deliveries': {
            'retained_delivery': {
                'sent_on': ('2001-01-01T00:00:01.234567+05:45', '2000-12-31T18:15:01.234567+00:00'),
                'delivered_on': ('2001-01-01T00:00:02.345678+05:45', '2000-12-31T18:15:02.345678+00:00'),
                'opened_on': ('2001-01-01T00:01:00.456789+05:45', '2000-12-31T18:16:00.456789+00:00'),
                'clicked_on': ('2001-01-01T00:01:01.567891+05:45', '2000-12-31T18:16:01.567891+00:00'),
            },
            'delete_bounced': {
                'sent_on': ('2001-01-01T00:00:01.678912-03:30', '2001-01-01T03:30:01.678912+00:00'),
                'bounced_on': ('2001-01-01T00:00:02.789123-03:30', '2001-01-01T03:30:02.789123+00:00'),
            },
        },
    }

    def _edit_timestamp_variant(self, variant):
        for group, section, pk_field in (
            ('messages', fixture.MESSAGE_SECTION, 'message_id'),
            ('deliveries', fixture.DELIVERY_SECTION, 'message_transaction_id'),
        ):
            replacements = {
                self.contract['ids'][name]: fields for name, fields in variant[group].items()
            }

            def edit(rows):
                for row in rows:
                    for field, (source_text, _) in replacements.get(row[pk_field], {}).items():
                        row[field] = source_text

            self._edit_section(section, edit)

    def _assert_timestamp_variant(self, association, variant, *, actor_id=None, audits=True):
        message_logs = LogEntry.objects.get_for_model(Message)
        self.assertEqual(message_logs.count(), 4 if audits else 0)
        self.assertFalse(LogEntry.objects.get_for_model(MessageTransaction).exists())
        for group, model, pk_field, fields in (
            ('messages', Message, 'message_id', ('created_at',)),
            ('deliveries', MessageTransaction, 'message_transaction_id',
             ('sent_on', 'delivered_on', 'opened_on', 'clicked_on', 'bounced_on')),
        ):
            replacements = {
                self.contract['ids'][name]: values for name, values in variant[group].items()
            }
            for source in self.expected[group]:
                # Read real persisted rows. Unchanged peers and explicit nulls
                # still use the fixed source expectation, never an import clock.
                row = model.objects.get(pk=source[pk_field])
                for field in fields:
                    with self.subTest(group=group, pk=str(row.pk), field=field):
                        expected_text = replacements.get(str(row.pk), {}).get(
                            field, (None, source[field]),
                        )[1]
                        expected = (None if expected_text is None
                                    else datetime.fromisoformat(expected_text))
                        actual = getattr(row, field)
                        self.assertEqual(actual, expected)
                        if expected is not None:
                            self.assertTrue(timezone.is_aware(actual))
                            self.assertEqual(actual.microsecond, expected.microsecond)
                if model is Message and audits:
                    event = message_logs.get(object_pk=str(row.pk))
                    self.assertEqual(event.action, LogEntry.Action.CREATE)
                    self.assertEqual(event.actor_id, actor_id)
                    self.assertEqual(event.changes['sport_association'], ['None', str(association.pk)])
                    # auditlog string changes normalize aware datetimes to UTC
                    # without an offset. Preserve all six fractional digits.
                    serialized = ('None' if expected is None else str(
                        expected.astimezone(datetime_timezone.utc).replace(tzinfo=None),
                    ))
                    self.assertEqual(event.changes['created_at'], ['None', serialized])
                    indexes = AuditLogIndex.objects.filter(log_entry=event)
                    self.assertEqual(indexes.count(), 1)
                    index = indexes.get()
                    self.assertEqual(index.sport_association_id, association.pk)
                    self.assertEqual(index.resolution_path, 'direct')

    def test_utc_microseconds_survive_import_and_create_audit_serialization(self):
        variant = self._UTC_FRACTIONAL_TIMESTAMPS
        self._edit_timestamp_variant(variant)
        self._assert_timestamp_variant(self._import(), variant)

    def test_non_utc_offsets_preserve_instants_microseconds_and_actor_audits(self):
        variant = self._OFFSET_FRACTIONAL_TIMESTAMPS
        self._edit_timestamp_variant(variant)
        actor = User.objects.create_user(
            username='synthetic-offset-actor', email='offset-actor@example.test',
        )
        with set_actor(actor):
            association = self._import()
        self._assert_timestamp_variant(association, variant, actor_id=actor.pk)

    def test_offset_microsecond_import_respects_enclosing_audit_disable(self):
        variant = self._OFFSET_FRACTIONAL_TIMESTAMPS
        self._edit_timestamp_variant(variant)
        before_logs = set(LogEntry.objects.values_list('pk', flat=True))
        before_indexes = set(AuditLogIndex.objects.values_list('pk', flat=True))
        with disable_auditlog():
            association = self._import()
        self._assert_timestamp_variant(association, variant, audits=False)
        self.assertEqual(set(LogEntry.objects.values_list('pk', flat=True)), before_logs)
        self.assertEqual(set(AuditLogIndex.objects.values_list('pk', flat=True)), before_indexes)
