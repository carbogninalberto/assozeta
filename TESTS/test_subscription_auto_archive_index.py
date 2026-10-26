"""Offline negative controls for the archive oracle, not Django/Go acceptance.

Synthetic rows exercise the same index assertions used with ORM readback. The
parent must run the production-Python/PostgreSQL adapter for runtime evidence.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from uuid import UUID

from TESTS.adapters.django.subscription_auto_archive import _check_audit_indexes


class ArchiveIndexEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.entries = []
        self.expected = {}
        self.indexes = {}
        self.resolutions = {}
        self.resolved_entries = []
        for number, model in enumerate(('subscription', 'payment', 'invoice'), 1):
            pk, association = UUID(int=number), UUID(int=100 + number)
            entry = SimpleNamespace(pk=number, object_pk=str(pk),
                                    content_type=SimpleNamespace(model=model), changes={})
            self.entries.append(entry)
            self.expected['application.' + model.capitalize()] = [
                {model + '_id': pk, 'sport_association_id': association}]
            path = 'direct' if model == 'subscription' else 'changes_json.sport_association'
            self.indexes[number] = [SimpleNamespace(log_entry_id=number,
                sport_association_id=association, resolution_path=path, created_at=self.now)]
            self.resolutions[number] = (SimpleNamespace(pk=association), path)
        self.ghosts = self.entries[1:]

    def check(self, *, delta=None):
        def resolve(entry):
            self.resolved_entries.append(entry.pk)
            return self.resolutions[entry.pk]

        _check_audit_indexes(self.entries, self.ghosts, self.expected,
            lambda entry: self.indexes[entry.pk], resolve, self.now,
            len(self.entries) if delta is None else delta)

    def test_django_historical_false_audits_require_fallback_indexes(self):
        self.check()
        self.assertEqual(self.resolved_entries, [2, 3])

    def test_old_missing_historical_indexes_are_rejected(self):
        self.indexes[2] = []
        self.indexes[3] = []
        # This is the former oracle's accepted state and count.
        with self.assertRaises(AssertionError):
            self.check(delta=1)

    def test_every_index_requires_exact_link_association_path_and_timestamp(self):
        for entry in self.entries:
            original = deepcopy(self.indexes[entry.pk])
            for field, wrong in (
                ('log_entry_id', 999),
                ('sport_association_id', UUID(int=999)),
                ('resolution_path', 'actor.sport_association'),
                ('created_at', self.now + timedelta(microseconds=1)),
            ):
                with self.subTest(model=entry.content_type.model, field=field):
                    self.indexes[entry.pk] = deepcopy(original)
                    setattr(self.indexes[entry.pk][0], field, wrong)
                    with self.assertRaises(AssertionError):
                        self.check()
            self.indexes[entry.pk] = original
        self.check()

    def test_each_event_requires_exactly_one_index(self):
        for entry in self.entries:
            original = self.indexes[entry.pk]
            for count in (0, 2):
                with self.subTest(model=entry.content_type.model, count=count):
                    self.indexes[entry.pk] = original * count
                    with self.assertRaises(AssertionError):
                        self.check()
            self.indexes[entry.pk] = original
        self.check()

    def test_total_count_includes_historical_events_and_excludes_extra_indexes(self):
        for delta in (1, 2, 4):
            with self.subTest(delta=delta), self.assertRaises(AssertionError):
                self.check(delta=delta)
        self.check(delta=3)

    def test_historical_readback_resolution_must_match_source_and_fallback_path(self):
        for entry in self.ghosts:
            original = self.resolutions[entry.pk]
            for wrong in ((SimpleNamespace(pk=UUID(int=999)), original[1]),
                          (original[0], 'direct'), (None, None)):
                with self.subTest(model=entry.content_type.model, resolution=wrong):
                    self.resolutions[entry.pk] = wrong
                    with self.assertRaises(AssertionError):
                        self.check()
            self.resolutions[entry.pk] = original
        self.check()

    def test_historical_fallback_cannot_be_reported_as_direct(self):
        for entry in self.ghosts:
            self.indexes[entry.pk][0].resolution_path = 'direct'
            with self.subTest(model=entry.content_type.model), self.assertRaises(AssertionError):
                self.check()
            self.indexes[entry.pk][0].resolution_path = 'changes_json.sport_association'
        self.check()

    def test_go_real_deltas_require_direct_indexes_without_fallback_resolution(self):
        self.ghosts = []
        for indexes in self.indexes.values():
            indexes[0].resolution_path = 'direct'
        self.check()
        self.assertEqual(self.resolved_entries, [])
        for entry in self.entries:
            self.indexes[entry.pk][0].resolution_path = 'changes_json.sport_association'
            with self.subTest(model=entry.content_type.model), self.assertRaises(AssertionError):
                self.check()
            self.indexes[entry.pk][0].resolution_path = 'direct'


if __name__ == '__main__':
    unittest.main()
