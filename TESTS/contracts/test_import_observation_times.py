"""Import parity preserves archived timestamps, even when the import runs immediately."""
from datetime import datetime, timedelta, timezone
import io
import json
import unittest
from unittest.mock import Mock
from zipfile import ZipFile

from TESTS.adapters.go.celery_consumer_import_process import Harness, Scenario


class ImportObservationTimes(unittest.TestCase):
    def test_fresh_archive_times_survive_both_worker_windows(self):
        archived = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
        audit_text = str(archived.replace(tzinfo=None))
        buffer = io.BytesIO()
        with ZipFile(buffer, 'w') as archive:
            archive.writestr('data/52_messages.json', json.dumps([{'created_at': archived.isoformat()}]))
        stack = Mock()
        stack.archives = {'rich': buffer.getvalue()}
        stack.broker_state.return_value = {}
        stack.cache.keys.return_value = []
        harness = Harness(stack, None)
        harness.before = {}
        harness.known = set()
        harness.bucket = Mock(return_value={})
        scenario = Scenario('fresh archive', 'rich')

        for offset in (0, 5):
            with self.subTest(offset=offset):
                start = archived + timedelta(seconds=offset)
                generated = start + timedelta(milliseconds=200)
                stack.snapshot.return_value = {
                    'communications.Message': [{'created_at': archived}],
                    'auditlog.LogEntry': [{'id': 1, 'changes': {'created_at': [None, audit_text]},
                                           'timestamp': generated}],
                }
                observed = harness.observe(scenario, b'{"headers":{"id":"fixture"}}',
                                           start, start + timedelta(seconds=1))
                self.assertEqual(observed['models']['communications.Message']['added'][0]['created_at'],
                                 archived.isoformat())
                self.assertEqual(observed['audit'][0]['changes']['created_at'], [None, audit_text])
                self.assertEqual(observed['audit'][0]['timestamp'], 'run')
                stack.snapshot.return_value['communications.Message'][0]['created_at'] += timedelta(microseconds=1)
                changed = harness.observe(scenario, b'{"headers":{"id":"fixture"}}',
                                          start, start + timedelta(seconds=1))
                self.assertNotEqual(changed['models'], observed['models'])
