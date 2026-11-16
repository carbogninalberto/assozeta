"""The refusal-reach analyzer and its HTTP/WebSocket gate.

The fixture module exercises the refusal shapes the analyzer must tell apart: a
direct return, a wrapped %w, a wrap through append, an interface dispatch, and a
sentinel used only with errors.Is.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BE_GO = ROOT / 'BE_GO'
SCRIPT = ROOT / 'tools/go-migration/refusal_reach.py'
FIXTURE = ROOT / 'TESTS/fixtures/refusal_reach'


def run_fixture_tool():
    completed = subprocess.run(
        ['go', 'run', './cmd/refusal-reach', '-dir', str(FIXTURE)],
        cwd=BE_GO,
        text=True,
        capture_output=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise AssertionError('refusal-reach failed: ' + completed.stderr)
    return completed.stdout


MISSING = object()


def run_gate(report, accepted, ceiling=None):
    """Run the wrapper against an existing report and an accepted list.

    ``ceiling`` is a ceiling file body, ``None`` to omit ``--ceiling``, or
    ``MISSING`` to pass the flag a path that does not exist.
    """
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        report_path = directory / 'report.json'
        report_path.write_text(json.dumps(report))
        accepted_path = directory / 'accepted.md'
        accepted_path.write_text(accepted)
        command = [sys.executable, str(SCRIPT), '--input', str(report_path),
                   '--accepted', str(accepted_path), '--http']
        if ceiling is not None:
            ceiling_path = directory / 'ceiling.json'
            if ceiling is not MISSING:
                ceiling_path.write_text(ceiling)
            command += ['--ceiling', str(ceiling_path)]
        return subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=120,
        )


REPORT = {'rows': [
    {'route': '/x', 'root_kind': 'http', 'reason': 'reason one', 'site': 'a.go:1'},
    {'route': '/ws/y/', 'root_kind': 'websocket', 'reason': 'reason two', 'site': 'b.go:2'},
    {'route': 'cleanup_job', 'root_kind': 'worker', 'reason': 'worker only', 'site': 'c.go:3'},
]}


class RefusalReachToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stdout = run_fixture_tool()
        cls.report = json.loads(cls.stdout)

    def test_direct_wrapped_and_interface_returns_are_rows(self):
        reasons = {row['reason'] for row in self.report['rows']}
        self.assertEqual(reasons, {'direct sentinel', 'wrapped sentinel', 'appended sentinel',
                                   'interface sentinel'})
        for row in self.report['rows']:
            self.assertEqual(row['root_kind'], 'command')
            self.assertEqual(row['route'], 'fixture')
            self.assertEqual(row['chain'][0], 'fixture/refusalreach/cmd/fixture.main')
        chains = {row['reason']: row['chain'] for row in self.report['rows']}
        self.assertEqual(chains['direct sentinel'][-1], 'fixture/refusalreach/cases.Direct')
        self.assertEqual(chains['wrapped sentinel'][-1], 'fixture/refusalreach/cases.Wrapped')
        self.assertEqual(chains['appended sentinel'][-1], 'fixture/refusalreach/cases.Appended')
        self.assertEqual(chains['interface sentinel'][-1],
                         '(fixture/refusalreach/cases.implementation).Run')

    def test_errors_is_only_sentinel_has_no_root(self):
        self.assertEqual([(row['sentinel'], row['reason']) for row in self.report['no_root']],
                         [('cases.ErrOnlyCheck', 'only errors.Is')])

    def test_output_is_deterministic(self):
        self.assertEqual(json.loads(run_fixture_tool()), self.report)


class RefusalReachGateTests(unittest.TestCase):
    def test_missing_and_empty_acceptance_files_fail(self):
        for accepted in ('', '# no entries\n'):
            completed = run_gate(REPORT, accepted)
            self.assertEqual(completed.returncode, 1, completed.stderr)
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / 'report.json'
            report_path.write_text(json.dumps(REPORT))
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), '--input', str(report_path),
                 '--accepted', str(Path(directory) / 'missing.md'), '--http'],
                cwd=ROOT, text=True, capture_output=True, timeout=120)
            self.assertEqual(completed.returncode, 1, completed.stderr)

    def test_worker_rows_are_not_gated(self):
        completed = run_gate(REPORT, '- worker only | * | background job\n')
        self.assertEqual(completed.returncode, 1)
        self.assertNotIn('cleanup_job', completed.stdout)

    def test_reason_can_be_accepted_per_route_or_for_all_routes(self):
        both = ('- reason one | /x | first\n'
                '- reason two | /ws/y/ | second\n')
        self.assertEqual(run_gate(REPORT, both).returncode, 0)
        wildcard = ('- reason one | * | first\n'
                    '- reason two | * | second\n')
        self.assertEqual(run_gate(REPORT, wildcard).returncode, 0)
        wrong_route = ('- reason one | /other | first\n'
                       '- reason two | * | second\n')
        completed = run_gate(REPORT, wrong_route)
        self.assertEqual(completed.returncode, 1)
        self.assertIn('/x', completed.stdout)

    def test_malformed_acceptance_line_is_an_error(self):
        completed = run_gate(REPORT, '- reason one\n')
        self.assertEqual(completed.returncode, 2)

    def test_example_inside_a_code_fence_is_not_an_entry(self):
        fenced = '```\n- reason one | * | example\n- reason two | * | example\n```\n'
        self.assertEqual(run_gate(REPORT, fenced).returncode, 1)
        accepted_example = '```\n- <reason string> | <route or *> | <why>\n```\n'
        completed = run_gate(REPORT, accepted_example)
        self.assertEqual(completed.returncode, 1)


class RefusalReachCeilingTests(unittest.TestCase):
    """The --http --ceiling ratchet: the committed count may only go down."""

    def test_above_the_ceiling_fails_and_prints_the_rows(self):
        completed = run_gate(REPORT, '', '{"http": 1}\n')
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn('/x | reason one | a.go:1', completed.stdout)
        self.assertIn('ceiling 1: a change added refusal rows', completed.stderr)

    def test_equal_to_the_ceiling_passes(self):
        completed = run_gate(REPORT, '', '{"http": 2}\n')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('2 HTTP/WebSocket refusal(s) not accepted (ceiling 2)', completed.stdout)

    def test_below_the_ceiling_fails_and_names_the_new_value(self):
        completed = run_gate(REPORT, '', '{"http": 5}\n')
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn('2 HTTP/WebSocket refusal(s) not accepted', completed.stderr)
        self.assertIn('below the ceiling 5', completed.stderr)
        self.assertIn('lower "http" in', completed.stderr)
        self.assertIn('to 2', completed.stderr)

    def test_accepted_rows_are_not_counted(self):
        completed = run_gate(REPORT, '- reason one | /x | first\n', '{"http": 1}\n')
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_missing_ceiling_file_is_an_error(self):
        completed = run_gate(REPORT, '', MISSING)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn('ceiling', completed.stderr)

    def test_malformed_ceiling_file_is_an_error(self):
        for ceiling in ('not json\n', '[]\n', '{"http": "many"}\n', '{"http": -1}\n',
                        '{"https": 0}\n'):
            with self.subTest(ceiling=ceiling):
                completed = run_gate(REPORT, '', ceiling)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertIn('ceiling', completed.stderr)

    def test_ceiling_requires_http(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            report_path = directory / 'report.json'
            report_path.write_text(json.dumps(REPORT))
            ceiling_path = directory / 'ceiling.json'
            ceiling_path.write_text('{"http": 2}\n')
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), '--input', str(report_path),
                 '--ceiling', str(ceiling_path)],
                cwd=ROOT, text=True, capture_output=True, timeout=120)
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertIn('--ceiling requires --http', completed.stderr)


if __name__ == '__main__':
    unittest.main()
