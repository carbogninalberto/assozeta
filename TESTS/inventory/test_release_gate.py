"""Fail-closed release inventory checks are necessary, never proof of full parity."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from TESTS.inventory.catalog import discover
from TESTS.inventory.release_gate import ReleaseGateError, check_release_inventory, load_ledger


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'BE/core/urls.py'
        self.source.parent.mkdir(parents=True)
        self.source.write_text("urlpatterns = [path('healthz', health), path('retired', retired)]\n")
        entries = discover(self.root)
        for entry in entries:
            if entry['declaration']['path'] == 'retired':
                entry.update(status='deprecated', notes=['Explicitly retired capability; no supported callers.'])
            else:
                entry.update(status='verified', go=['BE_GO/internal/handler/health.go'],
                             tests=['TESTS/contracts/test_health.py'],
                             evidence=[{'command': 'synthetic-fixture-contract', 'revision': 'a' * 40,
                                        'result': 'passed', 'artifact': 'synthetic-fixture-results.json'}])
                for field in ('authentication', 'permissions', 'resource_scope', 'side_effects', 'transaction'):
                    entry['contract'][field] = 'Explicit synthetic fixture contract'
        self.document = {'format': 1, 'entries': entries}

    def test_current_reviewed_inventory_meets_only_this_necessary_gate(self):
        before = copy.deepcopy(self.document)
        result = check_release_inventory(self.document, self.root)
        self.assertEqual(result, {'entries': 2, 'status': {'deprecated': 1, 'verified': 1}})
        self.assertEqual(self.document, before)

    def test_every_unfinished_status_blocks_publication(self):
        for status in ('uncharacterized', 'characterized', 'implementing', 'needs_review', 'source_removed'):
            with self.subTest(status=status):
                document = copy.deepcopy(self.document)
                document['entries'][0]['status'] = status
                with self.assertRaises(ReleaseGateError):
                    check_release_inventory(document, self.root)

    def test_verified_label_without_contract_or_passed_evidence_is_insufficient(self):
        changes = ({'go': []}, {'tests': []}, {'evidence': []},
                   {'evidence': [{'command': 'fixture', 'revision': 'a', 'result': 'failed', 'artifact': 'result'}]},
                   {'source_present': False})
        for change in changes:
            with self.subTest(change=change):
                document = copy.deepcopy(self.document)
                document['entries'][0].update(change)
                with self.assertRaises(ReleaseGateError):
                    check_release_inventory(document, self.root)
        document = copy.deepcopy(self.document)
        document['entries'][0]['contract']['resource_scope'] = None
        with self.assertRaises(ReleaseGateError):
            check_release_inventory(document, self.root)

    def test_deprecation_without_a_reviewed_rationale_is_insufficient(self):
        self.document['entries'][1]['notes'] = []
        with self.assertRaises(ReleaseGateError):
            check_release_inventory(self.document, self.root)

    def test_new_changed_or_removed_source_cannot_reuse_prior_verified_labels(self):
        for source in (
            "urlpatterns = [path('healthz', health), path('retired', retired), path('new', new)]\n",
            "# Changed source\nurlpatterns = [path('healthz', health), path('retired', retired)]\n",
            "urlpatterns = [path('healthz', health)]\n",
        ):
            with self.subTest(source=source):
                self.source.write_text(source)
                with self.assertRaisesRegex(ReleaseGateError, 'stale'):
                    check_release_inventory(self.document, self.root)

    def test_omitted_or_duplicate_entries_and_empty_inventory_are_rejected(self):
        for entries in ([], self.document['entries'][:1], self.document['entries'] * 2):
            with self.subTest(entries=len(entries)), self.assertRaises(ReleaseGateError):
                check_release_inventory({'format': 1, 'entries': entries}, self.root)
        self.source.unlink()
        with self.assertRaises(ReleaseGateError):
            check_release_inventory({'format': 1, 'entries': []}, self.root)

    def test_malformed_documents_fail_closed(self):
        for document in (None, [], {}, {'format': True, 'entries': []},
                         {'format': 2, 'entries': []}, {'format': 1, 'entries': None},
                         {'format': 1, 'entries': [{}]}, {'format': 1, 'entries': [None]}):
            with self.subTest(document=document), self.assertRaises(ReleaseGateError):
                check_release_inventory(document, self.root)

    def test_json_duplicate_keys_and_nonstandard_numbers_are_rejected(self):
        path = self.root / 'ledger.json'
        for raw in ('{"format":1,"format":2,"entries":[]}', '{"format":NaN,"entries":[]}',
                    '{"format":Infinity,"entries":[]}', '{'):
            with self.subTest(raw=raw):
                path.write_text(raw)
                with self.assertRaises(ReleaseGateError):
                    load_ledger(path)

    def test_cli_checks_supplied_inventory_without_rewriting_it(self):
        path = self.root / 'ledger.json'
        path.write_text(json.dumps(self.document))
        original = path.read_bytes()
        command = [sys.executable, '-m', 'TESTS.inventory.release_gate', '--root', str(self.root), '--ledger', str(path)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['scope'], 'necessary inventory gate; not full migration acceptance')
        self.assertEqual(path.read_bytes(), original)
        self.document['entries'][0]['status'] = 'implementing'
        path.write_text(json.dumps(self.document))
        original = path.read_bytes()
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('incomplete', result.stderr)
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
