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

    def test_summary_renders_why_workflows_were_captured_fresh(self):
        report = timing.summarize(None, {'mode': 'full'}, {
            'fresh_workflows': ['a', 'b', 'c'], 'reused_workflows': [], 'reused_count': 2,
            'fresh_reasons': {'miss: no evidence at this fingerprint': 2,
                              'impact: full verification required': 1}})
        self.assertIn('**Why workflows were captured fresh:**', report)
        self.assertIn('| Reason | Workflows |', report)
        self.assertIn('| miss: no evidence at this fingerprint | 2 |', report)
        self.assertIn('| impact: full verification required | 1 |', report)
        self.assertLess(report.index('miss: no evidence'), report.index('impact: full'))
        self.assertLess(report.index('**Evidence:**'), report.index('**Why workflows were captured fresh:**'))

    def test_fresh_reason_table_is_bounded_at_ten_rows_and_sanitizes_paths(self):
        reasons = {f'reason {index:02d}': 1 for index in range(12)}
        reasons['changed /home/runner/work/x'] = 5
        report = timing.summarize(None, None, {'fresh_reasons': reasons})
        rows = [line for line in report.splitlines() if line.startswith('| reason ') or line.startswith('| changed ')]
        self.assertEqual(len(rows), 10)
        self.assertIn('| changed [local path] | 5 |', report)
        self.assertIn('... 3 more', report)
        self.assertNotIn('/home/runner', report)

    def test_summary_without_fresh_reasons_is_unchanged(self):
        cache = {'fresh_workflows': ['a'], 'reused_workflows': []}
        self.assertNotIn('Why workflows were captured fresh', timing.summarize(None, None, cache))
        self.assertNotIn('Why workflows were captured fresh',
                         timing.summarize(None, None, {**cache, 'fresh_reasons': {}}))
        self.assertEqual(timing.summarize(None, None, cache), timing.summarize(None, None, dict(cache)))

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


SPANS = (
    {'scope': 'scenario', 'scenario': 'alpha', 'status': 'passed', 'browser_launch_ms': 1000,
     'actions_ms': 4000, 'total_ms': 5500, 'checkpoints': 2},
    {'scope': 'scenario', 'scenario': 'beta', 'status': 'failed', 'browser_launch_ms': 500,
     'actions_ms': 1000, 'total_ms': 2000, 'checkpoints': 1},
    {'scope': 'checkpoint', 'scenario': 'alpha', 'checkpoint': 'first', 'settle_ms': 100, 'ready_ms': 200,
     'first_shot_ms': 300, 'stability_ms': 300, 'stability_shots': 4, 'write_ms': 100, 'crop_ms': 100,
     'hash_ms': 100, 'total_ms': 1200},
    {'scope': 'checkpoint', 'scenario': 'alpha', 'checkpoint': 'second', 'settle_ms': 100, 'ready_ms': 100,
     'first_shot_ms': 200, 'stability_ms': 300, 'stability_shots': 3, 'write_ms': 100, 'crop_ms': 100,
     'hash_ms': 100, 'total_ms': 1000},
    {'scope': 'checkpoint', 'scenario': 'beta', 'checkpoint': 'only', 'settle_ms': 100, 'ready_ms': 100,
     'first_shot_ms': 100, 'stability_ms': 100, 'stability_shots': 2, 'write_ms': 100, 'crop_ms': 100,
     'hash_ms': 100, 'total_ms': 700},
)


def write_spans(path, spans):
    Path(path).write_text(''.join(json.dumps(span) + '\n' for span in spans))
    return Path(path)


class SpanTests(unittest.TestCase):
    def test_span_file_sits_beside_the_timing_file_only_when_one_is_named(self):
        self.assertIsNone(timing.span_file_for(None))
        self.assertIsNone(timing.span_file_for(''))
        self.assertEqual(timing.span_file_for(Path('/run/timings.jsonl')),
                         Path('/run/timings.jsonl' + timing.SPAN_SUFFIX))
        self.assertEqual(timing.span_file_for('run/timings.jsonl'),
                         Path('run/timings.jsonl.spans.jsonl'))

    def test_read_spans_ignores_missing_files_unusable_lines_and_string_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run.spans.jsonl'
            self.assertEqual(timing.read_spans(path), [])
            self.assertEqual(timing.read_spans(None), [])
            path.write_text(
                json.dumps(SPANS[2]) + '\n'
                + '{"scope": "other", "scenario": "alpha"}\n'
                + '[1]\n'
                + '{"scope": "checkpoint", "scenario": "alpha", "checkpoint": "string", "settle_ms": "100", '
                  '"ready_ms": 200, "first_shot_ms": 300, "stability_ms": 300, "write_ms": 100, '
                  '"crop_ms": 100, "hash_ms": 100, "total_ms": 1200}\n'
                + '{"scope": "scenario", "scenario": "truncated", "browser_launch_ms": 100,\n')
            spans = timing.read_spans(path)
        self.assertEqual([(span['scope'], span['checkpoint']) for span in spans], [('checkpoint', 'first')])

    def test_summarize_spans_splits_capture_cost_in_seconds(self):
        self.assertEqual(timing.summarize_spans(SPANS), {
            'scenarios': 2, 'checkpoints': 3,
            'scenario_total': 7.5, 'browser_launch': 1.5, 'actions': 5.0, 'capture': 2.9,
            'non_capture_actions': 2.1,
            'settle': 0.3, 'ready': 0.4, 'first_shot': 0.6, 'stability': 0.7,
            'write': 0.3, 'crop': 0.3, 'hash': 0.3,
            'stability_shots': 9,
            'slowest': [{'scenario': 'alpha', 'total': 5.5, 'capture': 2.2, 'checkpoints': 2},
                        {'scenario': 'beta', 'total': 2.0, 'capture': 0.7, 'checkpoints': 1}],
        })

    def test_summarize_spans_of_empty_input_is_zeros_and_never_divides_by_zero(self):
        zeros = {'scenarios': 0, 'checkpoints': 0, 'scenario_total': 0.0, 'browser_launch': 0.0,
                 'actions': 0.0, 'capture': 0.0, 'non_capture_actions': 0.0, 'settle': 0.0, 'ready': 0.0,
                 'first_shot': 0.0, 'stability': 0.0, 'write': 0.0, 'crop': 0.0, 'hash': 0.0,
                 'stability_shots': 0, 'slowest': []}
        self.assertEqual(timing.summarize_spans([]), zeros)
        self.assertEqual(timing.summarize_spans(None), zeros)
        report = timing.summarize(None, {'mode': 'behavioral'}, None)
        self.assertNotIn('Capture cost split', report)

    def test_slowest_scenarios_are_bounded_and_ties_break_on_name(self):
        spans = [{'scope': 'scenario', 'scenario': 's' + str(index), 'status': 'passed',
                  'browser_launch_ms': 0, 'actions_ms': 0, 'total_ms': (7 - index) * 1000, 'checkpoints': 0}
                 for index in range(1, 7)]
        spans.append({'scope': 'scenario', 'scenario': 'zzz', 'status': 'passed', 'browser_launch_ms': 0,
                      'actions_ms': 0, 'total_ms': 6000, 'checkpoints': 0})
        slowest = timing.summarize_spans(spans)['slowest']
        self.assertEqual([item['scenario'] for item in slowest], ['s1', 'zzz', 's2', 's3', 's4'])


class SpanSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.file = self.directory / 'timings.jsonl'
        timing.record('capture', 30.0, {'status': 'passed'}, file=self.file)
        self.spans = self.directory / 'timings.jsonl.spans.jsonl'

    def tearDown(self):
        self.temporary.cleanup()

    def test_capture_split_is_appended_only_when_the_spans_file_exists(self):
        baseline = timing.summarize(self.file, None, None)
        self.assertNotIn('Capture cost split', baseline)
        write_spans(self.spans, SPANS)
        report = timing.summarize(self.file, None, None)
        self.assertTrue(report.startswith(baseline.rstrip('\n') + '\n\n**Capture cost split (summed over 2 scenarios, 3 checkpoints):**'))
        self.assertIn('| Browser launch | 1.5 s | 20.0% |', report)
        self.assertIn('| Actions outside screenshots | 2.1 s | 28.0% |', report)
        self.assertIn('| Screenshot capture (total) | 2.9 s | 38.7% |', report)
        self.assertIn('| &nbsp;&nbsp;settle | 0.3 s | 4.0% |', report)
        self.assertIn('| &nbsp;&nbsp;first shot | 0.6 s | 8.0% |', report)
        self.assertIn('| &nbsp;&nbsp;stability | 0.7 s | 9.3% |', report)
        self.assertIn('- `alpha` - 5.5 s total, 2.2 s capture, 2 checkpoints', report)
        self.assertIn('- `beta` - 2.0 s total, 0.7 s capture, 1 checkpoint', report)
        self.assertIn('stability includes the fixed 100 ms waits', report)
        self.spans.unlink()
        self.assertEqual(timing.summarize(self.file, None, None), baseline)

    def test_capture_split_publishes_ids_without_local_paths_or_secrets(self):
        write_spans(self.spans, [
            {'scope': 'scenario', 'scenario': '/home/runner/work/assozeta/assozeta', 'status': 'passed',
             'browser_launch_ms': 1000, 'actions_ms': 1000, 'total_ms': 2000, 'checkpoints': 1},
            {'scope': 'checkpoint', 'scenario': '/home/runner/work/assozeta/assozeta', 'checkpoint': 'members',
             'settle_ms': 100, 'ready_ms': 100, 'first_shot_ms': 100, 'stability_ms': 100,
             'stability_shots': 2, 'write_ms': 100, 'crop_ms': 100, 'hash_ms': 100, 'total_ms': 700},
        ])
        report = timing.summarize(self.file, None, None)
        section = report[report.index('**Capture cost split'):]
        self.assertIn('[local path]', section)
        for forbidden in ('/home/runner', str(self.directory)):
            self.assertNotIn(forbidden, section)
        for forbidden in ('secret', 'token', 'password', 'api_key'):
            self.assertNotIn(forbidden, section.lower())


if __name__ == '__main__':
    unittest.main()
