"""promote_verified.py credits only executed, passing, current receipts and refuses everything else."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from TESTS.inventory.catalog import ROOT, validate

SPEC = importlib.util.spec_from_file_location('promote_verified', ROOT / 'tools/go-migration/promote_verified.py')
promote_verified = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promote_verified)
TRANSPORTS = ['django-native', 'go-native', 'caddy-v1', 'caddy-v2']
CONTRACT = {field: f'Synthetic {field}' for field in promote_verified.REQUIRED}


def entry(identifier, tests, status='implementing', contract=None, **extra):
    return {'id': identifier, 'kind': identifier.split(':')[0], 'source_present': True, 'status': status,
            'django': {'file': 'BE/x.py', 'line': 1, 'symbol': identifier.split(':')[-1], 'sha256': 'a'},
            'declaration': {'path': 'x'}, 'go': ['BE_GO/x.go'], 'dependencies': [], 'tests': tests,
            'contract': {'methods': None, 'external_paths': None, 'request': None, 'response': None, 'errors': None,
                         **(CONTRACT if contract is None else contract), 'execution_owner': 'django'},
            'evidence': ['docs/x.md', {'revision': 'old', 'result': 'RED: Go absent.', 'artifact': 'red.json'}],
            'notes': [], **extra}


class PromoteVerifiedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        git = lambda *args: subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()
        git('init', '-q')
        self.write('TESTS/fixtures/foo_bar_http.json', {'cases': [{'name': 'a'}, {'name': 'b'}]})
        self.write('BE/x.py', 'x = 1\n')
        self.write('.gitignore', 'quality-reports/\n*.json.tmp\n/ledger.json\n/patch.json\n/sweep.json\n/empty.json\n')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@example.test', 'commit', '-qm', 'base')
        self.head = git('rev-parse', 'HEAD')
        self.git = git
        self.write('quality-reports/go-migration/foo-bar-http-sweep-L/results.json',
                   {'success': True, 'workloads': [{'label': t, 'cases': [{'case': 'a'}, {'case': 'b'}]} for t in TRANSPORTS]})
        self.write('quality-reports/go-migration/sweep-L/process-baz_process.log', 'build ok\n{"success": true, "cases": 3}\n')
        receipt = self.write('quality-reports/go-migration/ledger-verified-r/cmd/1-results.json', {'success': True})
        self.sweep = {'head': self.head, 'label': 'L', 'total': 2, 'results': {
            'http:foo-bar': {'ok': True}, 'process:baz_process': {'ok': True}}}
        self.components = {'head': self.head, 'results': {'component:cmd': {
            'ok': True, 'entries': ['command:BE/x.py:cmd'],
            'commands': [{'command': 'python -m cmd', 'ok': True, 'check': 'success=True',
                          'artifact': 'quality-reports/go-migration/ledger-verified-r/cmd/1-results.json',
                          'receipt_sha256': promote_verified.sha256(receipt)}]}}}
        self.document = {'format': 1, 'entries': [
            entry('route:BE/x.py:foo', ['TESTS/fixtures/foo_bar_http.json', 'TESTS/adapters/go/baz_process.py']),
            entry('command:BE/x.py:cmd', ['TESTS/adapters/go/cmd.py']),
            entry('signal:BE/x.py:orphan', ['BE_GO/x_test.go']),
            entry('route:BE/x.py:dep', [], status='deprecated', notes=['Retired.'])]}
        self.patch = {'contract': {}, 'drop_source_removed': {}, 'hold': {}}

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def run_promote(self, document=None, patch=None, sweep=None, components=None):
        return promote_verified.promote(document or self.document, patch or self.patch, sweep or self.sweep,
                                        [self.components] if components is None else components, root=self.root)

    def by_id(self, identifier):
        return next(e for e in self.document['entries'] if e['id'] == identifier)

    def test_passing_current_receipts_promote_with_valid_evidence(self):
        report = self.run_promote()
        self.assertEqual(report['promoted'], ['route:BE/x.py:foo', 'command:BE/x.py:cmd'])
        self.assertEqual(list(report['blocked']), ['signal:BE/x.py:orphan'])
        route = self.by_id('route:BE/x.py:foo')
        self.assertEqual(route['status'], 'verified')
        self.assertEqual([e['artifact'] for e in route['evidence']], [
            'quality-reports/go-migration/foo-bar-http-sweep-L/results.json',
            'quality-reports/go-migration/sweep-L/process-baz_process.log'])
        self.assertTrue(all(e['result'] == 'passed' and e['revision'] == self.head and e['command'] for e in route['evidence']))
        self.assertIn('Implementing evidence: docs/x.md', route['notes'])
        self.assertIn('Implementing evidence: RED: Go absent. (red.json)', route['notes'])
        validate(self.document['entries'])

    def test_failed_missing_or_short_results_block(self):
        cases = {
            'failed': {**self.sweep, 'results': {**self.sweep['results'], 'http:foo-bar': {'ok': False, 'last': 'boom'}}},
            'missing': {**self.sweep, 'results': {'process:baz_process': {'ok': True}}},
        }
        for name, sweep in cases.items():
            with self.subTest(name):
                document = copy.deepcopy(self.document)
                report = self.run_promote(document=document, sweep=sweep)
                self.assertNotIn('route:BE/x.py:foo', report['promoted'])
                self.assertEqual(document['entries'][0]['status'], 'implementing')
        self.write('quality-reports/go-migration/foo-bar-http-sweep-L/results.json',
                   {'success': True, 'workloads': [{'label': t, 'cases': [{'case': 'a'}]} for t in TRANSPORTS]})
        report = self.run_promote()
        self.assertIn('not a 2-case pass', ' '.join(report['blocked']['route:BE/x.py:foo']))

    def test_changed_component_receipt_or_failed_command_blocks(self):
        self.write('quality-reports/go-migration/ledger-verified-r/cmd/1-results.json', {'success': False})
        report = self.run_promote()
        self.assertIn('receipt missing or changed', report['blocked']['command:BE/x.py:cmd'][0])
        components = copy.deepcopy(self.components)
        components['results']['component:cmd'].update(ok=False)
        report = self.run_promote(document=copy.deepcopy(self.document), components=[components])
        self.assertIn('command:BE/x.py:cmd', report['blocked'])

    def test_hold_and_incomplete_contract_block(self):
        self.patch['hold'] = {'route:BE/x.py:foo': 'notes say partial'}
        self.by_id('command:BE/x.py:cmd')['contract']['transaction'] = None
        report = self.run_promote()
        self.assertEqual(report['promoted'], [])
        self.assertIn('held: notes say partial', report['blocked']['route:BE/x.py:foo'])
        self.assertIn('contract lacks transaction', report['blocked']['command:BE/x.py:cmd'])

    def test_contract_patch_fills_nulls_and_never_overwrites(self):
        self.by_id('command:BE/x.py:cmd')['contract']['transaction'] = None
        self.patch['contract'] = {'command:BE/x.py:cmd': {'transaction': 'None.', 'permissions': CONTRACT['permissions']}}
        report = self.run_promote()
        self.assertEqual(report['filled_contract_fields'], 1)
        self.assertIn('command:BE/x.py:cmd', report['promoted'])
        self.patch['contract'] = {'route:BE/x.py:foo': {'permissions': 'Different.'}}
        with self.assertRaisesRegex(promote_verified.PromotionError, 'overwrite'):
            self.run_promote(document=copy.deepcopy(self.document))

    def test_product_change_after_the_recorded_head_is_refused(self):
        self.write('BE/x.py', 'x = 2\n')
        with self.assertRaisesRegex(promote_verified.PromotionError, 'product files changed'):
            self.run_promote()

    def test_source_removed_is_dropped_only_when_covered(self):
        removed = entry('schedule:BE/a.py:job', [], status='source_removed', source_present=False)
        cover = entry('schedule:BE/b.py:job', ['TESTS/fixtures/foo_bar_http.json'])
        removed['declaration'] = cover['declaration'] = {'schedule': "{'task': 't', 'schedule': 60.0}"}
        self.document['entries'] += [removed, cover]
        self.patch['drop_source_removed'] = {removed['id']: {'covered_by': cover['id'], 'note': 'Moved unchanged.'}}
        report = self.run_promote(document=copy.deepcopy(self.document))
        self.assertEqual(report['dropped'], [removed['id']])
        cover['declaration'] = {'schedule': "{'task': 'other', 'schedule': 60.0}"}
        with self.assertRaisesRegex(promote_verified.PromotionError, 'declaring something else'):
            self.run_promote()

    def test_cli_refuses_empty_inputs_dry_runs_and_requires_a_complete_sweep(self):
        ledger = self.write('ledger.json', self.document)
        patch = self.write('patch.json', {'hold': {'x': 'y'}})
        sweep = self.write('sweep.json', self.sweep)
        empty = self.write('empty.json', '')
        before = ledger.read_text()
        argv = ['promote_verified.py', '--ledger', str(ledger), '--patch', str(patch), '--sweep']
        with mock.patch.object(promote_verified, 'ROOT', self.root), mock.patch('builtins.print'):
            for bad in (empty, self.root / 'absent.json'):
                with mock.patch.object(sys, 'argv', argv + [str(bad)]):
                    self.assertEqual(promote_verified.main(), 97)
            with mock.patch.object(sys, 'argv', argv + [str(sweep)]), \
                    mock.patch.object(promote_verified, 'promote', lambda d, p, s, c: {'promoted': ['x']}):
                self.assertEqual(promote_verified.main(), 0)
            self.assertEqual(ledger.read_text(), before)
            self.write('sweep.json', {**self.sweep, 'total': 3})
            with mock.patch.object(sys, 'argv', argv + [str(sweep), '--apply']):
                self.assertEqual(promote_verified.main(), 97)
        self.assertEqual(ledger.read_text(), before)


if __name__ == '__main__':
    unittest.main()
