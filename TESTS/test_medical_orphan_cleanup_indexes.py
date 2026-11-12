"""Offline index-oracle negative controls; not actual Django/Go acceptance.

Exercise the same snapshot checker used after the real task. The parent runs
the full production-Python accounts adapter for database/file/cascade evidence.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from uuid import UUID

from TESTS.adapters.django import medical_orphan_cleanup as oracle


INDEX = 'application.AuditLogIndex'
ARCHIVE = 'application.SportAssociationDocumentsArchive'


class MedicalCleanupIndexTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.archive_id, self.association = UUID(int=600), UUID(int=11)
        self.prior = dict(audit_log_index_id=UUID(int=1, version=4),
                          log_entry_id=10, sport_association_id=UUID(int=10),
                          resolution_path='direct', created_at=self.now - timedelta(days=1))
        self.before = {INDEX: [deepcopy(self.prior)], ARCHIVE: [
            dict(sport_association_documents_archive_id=self.archive_id,
                 sport_association_id=self.association)]}
        self.entries = [SimpleNamespace(pk=20 + i, object_pk=str(UUID(int=100 + i)),
            content_type=SimpleNamespace(app_label=app, model=model),
            changes={}, timestamp=self.now) for i, (app, model) in enumerate((
                ('docmanager', 'document'), ('application', 'medicalcertificate'),
                ('application', 'medicalcertificate'),
                ('application', 'sportassociationdocumentsarchive')))]
        self.event = self.entries[-1]
        self.event.object_pk = str(self.archive_id)
        self.event.changes = {'sport_association': [str(self.association), 'None']}
        self.new_index = dict(audit_log_index_id=UUID(int=2, version=4),
            log_entry_id=self.event.pk, sport_association_id=self.association,
            resolution_path='changes_json.sport_association', created_at=self.now)
        self.after = {INDEX: [deepcopy(self.prior), deepcopy(self.new_index)], ARCHIVE: []}

    def check(self, kind='archive'):
        oracle._check_audit_indexes(kind, self.entries, self.before, self.after, self.now)

    def test_archive_delete_requires_exactly_one_fallback_index(self):
        self.check()

    def test_old_missing_archive_index_is_rejected(self):
        self.after[INDEX].pop()
        with self.assertRaises(AssertionError):
            self.check()

    def test_wrong_link_association_path_timestamp_or_identifier_is_rejected(self):
        for field, wrong in (
            ('log_entry_id', self.entries[0].pk),
            ('log_entry_id', self.prior['log_entry_id']),
            ('sport_association_id', self.prior['sport_association_id']),
            ('resolution_path', 'direct'),
            ('resolution_path', 'actor.sport_association'),
            ('created_at', self.now - timedelta(microseconds=1)),
            ('created_at', self.now + timedelta(microseconds=1)),
            ('created_at', self.now.replace(tzinfo=None)),
            ('audit_log_index_id', UUID(int=2)),
            ('audit_log_index_id', self.prior['audit_log_index_id']),
        ):
            with self.subTest(field=field, wrong=wrong):
                self.after[INDEX][-1] = dict(self.new_index, **{field: wrong})
                with self.assertRaises(AssertionError):
                    self.check()
        self.after[INDEX][-1] = deepcopy(self.new_index)
        self.check()

    def test_duplicate_or_unrelated_extra_index_is_rejected(self):
        for log_id in (self.event.pk, self.entries[0].pk, self.entries[1].pk, 999):
            with self.subTest(log_id=log_id):
                self.after[INDEX].append(dict(self.new_index,
                    audit_log_index_id=UUID(int=3, version=4), log_entry_id=log_id))
                with self.assertRaises(AssertionError):
                    self.check()
                self.after[INDEX].pop()

    def test_every_field_of_every_prior_index_is_preserved(self):
        for field, wrong in (
            ('audit_log_index_id', UUID(int=9, version=4)),
            ('log_entry_id', 999), ('sport_association_id', self.association),
            ('resolution_path', 'changes_json.sport_association'),
            ('created_at', self.now),
        ):
            with self.subTest(field=field):
                self.after[INDEX][0] = dict(self.prior, **{field: wrong})
                with self.assertRaises(AssertionError):
                    self.check()
        self.after[INDEX] = [deepcopy(self.new_index)]
        with self.assertRaises(AssertionError):
            self.check()
        self.after[INDEX] = [deepcopy(self.prior), deepcopy(self.prior), deepcopy(self.new_index)]
        with self.assertRaises(AssertionError):
            self.check()

    def test_multiple_prior_indexes_survive_with_new_uuid_between_them(self):
        last = dict(self.prior, audit_log_index_id=UUID(int=3, version=4),
                    log_entry_id=11, sport_association_id=UUID(int=12))
        self.before[INDEX].append(deepcopy(last))
        self.after[INDEX].append(deepcopy(last))
        self.check()
        self.after[INDEX][-1]['resolution_path'] = 'actor.sport_association'
        with self.assertRaises(AssertionError):
            self.check()

    def test_archive_event_must_match_original_object_and_association(self):
        for field, wrong in (
            ('object_pk', str(UUID(int=999))),
            ('content_type', SimpleNamespace(app_label='other', model='sportassociationdocumentsarchive')),
            ('content_type', SimpleNamespace(app_label='application', model='medicalcertificate')),
            ('changes', {'sport_association': [str(UUID(int=999)), 'None']}),
            ('changes', {'sport_association': [str(self.association), str(self.association)]}),
            ('timestamp', self.now + timedelta(microseconds=1)),
        ):
            original = getattr(self.event, field)
            with self.subTest(field=field):
                setattr(self.event, field, wrong)
                with self.assertRaises(AssertionError):
                    self.check()
            setattr(self.event, field, original)
        self.event.changes['sport_association'] = (str(self.association), 'None')
        self.check()
        self.entries.append(deepcopy(self.event))
        with self.assertRaises(AssertionError):
            self.check()

    def test_every_other_scenario_forbids_any_new_index(self):
        for kind in ('orphan', 'active', 'historical', 'draft', 'upload-grant',
                     'shared', 'invoice', 'attachment', 'null', 'missing',
                     'filepath', 'storage-failure', 'database-failure'):
            with self.subTest(kind=kind):
                self.entries = self.entries[:3]
                self.before[ARCHIVE] = []
                self.after[INDEX] = [deepcopy(self.prior)]
                self.check(kind)
                for log_id in (self.entries[0].pk, self.entries[1].pk, 999):
                    self.after[INDEX] = [deepcopy(self.prior),
                        dict(self.new_index, log_entry_id=log_id)]
                    with self.assertRaises(AssertionError):
                        self.check(kind)


if __name__ == '__main__':
    unittest.main()
