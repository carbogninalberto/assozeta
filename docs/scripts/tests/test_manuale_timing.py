"""Stage timing records and the GitHub job summary use measured values only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'docs/scripts'))
import manuale_timing as timing  # noqa: E402


class StageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.file = Path(self.directory.name) / 'timing.jsonl'

    def tearDown(self):
        self.directory.cleanup()

    def test_stage_appends_one_json_line_with_the_contract_keys(self):
        with patch.dict(os.environ, {timing.FILE_ENVIRONMENT: str(self.file)}):
            with timing.Stage('compile', {'pages': 3}) as stage:
                stage.detail['fresh'] = 1
            with timing.Stage('index'):
                pass
        lines = [json.loads(line) for line in self.file.read_text().splitlines()]
        self.assertEqual([entry['stage'] for entry in lines], ['compile', 'index'])
        for entry in lines:
            self.assertEqual(set(entry), {'stage', 'seconds', 'detail'})
            self.assertIsInstance(entry['seconds'], float)
            self.assertGreaterEqual(entry['seconds'], 0)
            self.assertEqual(entry['detail']['status'], 'passed')
            self.assertLessEqual(entry['detail']['started_at'], entry['detail']['ended_at'])
        self.assertEqual((lines[0]['detail']['pages'], lines[0]['detail']['fresh']), (3, 1))

    def test_failed_stage_is_recorded_and_the_error_propagates(self):
        with self.assertRaises(RuntimeError):
            with timing.Stage('capture', file=self.file):
                raise RuntimeError('boom /home/runner/private')
        entry = json.loads(self.file.read_text())
        self.assertEqual(entry['detail']['status'], 'failed')
        self.assertEqual(entry['detail']['error'], 'RuntimeError')
        self.assertNotIn('private', self.file.read_text())

    def test_no_timing_file_disables_recording(self):
        with patch.dict(os.environ, {}, clear=True):
            with timing.Stage('plan') as stage:
                pass
        self.assertIsNone(stage.entry)

    def test_cli_stage_propagates_exit_status_and_records_it(self):
        environment = {**os.environ, timing.FILE_ENVIRONMENT: str(self.file)}
        script = str(ROOT / 'docs/scripts/manuale_timing.py')
        ok = subprocess.run([sys.executable, script, 'stage', 'ok', '--', sys.executable, '-c', 'pass'], env=environment)
        bad = subprocess.run([sys.executable, script, 'stage', 'bad', '--detail', 'kind=test', '--',
                              sys.executable, '-c', 'raise SystemExit(3)'], env=environment)
        self.assertEqual((ok.returncode, bad.returncode), (0, 3))
        entries = timing.read(self.file)
        self.assertEqual([(entry['stage'], entry['detail']['status'], entry['detail']['exit_code']) for entry in entries],
                         [('ok', 'passed', 0), ('bad', 'failed', 3)])
        self.assertEqual(entries[1]['detail']['kind'], 'test')

    def test_truncated_or_foreign_lines_are_ignored(self):
        self.file.write_text('{"stage": "a", "seconds": 1.5, "detail": {}}\n{"stage": \nnot json\n[1]\n')
        self.assertEqual([entry['stage'] for entry in timing.read(self.file)], ['a'])
        self.assertEqual(timing.read(Path(self.directory.name) / 'missing.jsonl'), [])


class SummaryTests(unittest.TestCase):
    def test_summary_reports_mode_reasons_evidence_cache_sizes_and_total(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            file = directory / 'timing.jsonl'
            timing.record('plan', 2.0, {'status': 'passed', 'started_at': 100.0, 'ended_at': 102.0}, file=file)
            timing.record('capture', 65.5, {'status': 'failed', 'started_at': 102.0, 'ended_at': 170.0}, file=file)
            impact = directory / 'impact.json'
            impact.write_text(json.dumps({'format': 1, 'mode': 'behavioral', 'full_verification_required': False,
                'affected_workflows': ['member-tags'], 'reused_workflows': ['archive', 'two-factor'],
                'affected_pages': ['manuale/docs/soci.mdx'], 'checks': {'bot': False, 'mcp': True, 'security': False, 'runtime': True},
                'files': [], 'reasons': ['UI/src/routes/tags | changed', 'see /home/runner/work/x/y.json']}))
            cache = directory / 'cache.json'
            cache.write_text(json.dumps({'hits': [{'name': 'trusted baseline', 'detail': 'producer run 7'}],
                                         'misses': [{'name': 'frontend build', 'detail': 'UI changed'}],
                                         'artifacts': {'verified baseline': 3 * 1024 * 1024}}))
            package = directory / 'package'
            package.mkdir()
            (package / 'index.json').write_bytes(b'x' * 2048)
            report = timing.summarize(file, impact, cache, artifacts={'runtime package': package,
                                                                      'absent': directory / 'none'})
        self.assertIn('**Mode:** `behavioral`', report)
        self.assertIn('UI/src/routes/tags \\| changed', report)
        self.assertIn('[local path]', report)
        self.assertNotIn('/home/runner', report)
        self.assertIn('Planned fresh workflows: 1 - `member-tags`', report)
        self.assertIn('Planned reused workflows: 2', report)
        self.assertIn('Planned fresh pages: 1', report)
        self.assertIn('mcp=yes', report)
        self.assertIn('1 hit(s), 1 miss(es)', report)
        self.assertIn('| verified baseline | 3.0 MiB |', report)
        self.assertIn('| runtime package | 2.0 KiB |', report)
        self.assertNotIn('absent', report)
        self.assertIn('| capture | failed | 1 min 05.5 s |', report)
        self.assertIn('**Total measured stage time:** 1 min 07.5 s; wall clock across stages: 1 min 10.0 s', report)

    def test_recorded_run_decisions_replace_the_plan(self):
        report = timing.summarize(None, {'mode': 'behavioral', 'affected_workflows': ['a', 'b']},
                                  {'fresh_workflows': ['a'], 'reused_workflows': ['b', 'c'], 'fresh_pages': [],
                                   'reused_pages': ['p']})
        self.assertIn('- Fresh workflows: 1 - `a`', report)
        self.assertIn('- Reused workflows: 2', report)
        self.assertIn('- Fresh pages: 0', report)
        self.assertIn('- Reused pages: 1', report)
        self.assertNotIn('Planned', report)

    def test_summary_tolerates_missing_inputs_and_flags_fallback(self):
        report = timing.summarize(None, {'mode': 'full', 'fallback': True, 'full_verification_required': True}, None)
        self.assertIn('failed closed', report)
        self.assertIn('No stage timings were recorded.', report)
        self.assertIn('(full verification required)', report)
        self.assertIn('**Mode:** `unknown`', timing.summarize('/nonexistent', '/nonexistent', '/nonexistent'))

    def test_cli_summary_appends_to_the_step_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'summary.md'
            output.write_text('previous\n')
            file = Path(directory) / 'timing.jsonl'
            timing.record('offline:python', 1.25, {'status': 'passed'}, file=file)
            self.assertEqual(timing.main(['summary', '--timing', str(file), '--title', 'Offline', '--output', str(output)]), 0)
            text = output.read_text()
        self.assertTrue(text.startswith('previous\n### Offline'))
        self.assertIn('| offline:python | passed | 1.2 s |', text)


if __name__ == '__main__':
    unittest.main()
