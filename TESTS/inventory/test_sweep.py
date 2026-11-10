"""sweep.py splits the job list into disjoint, complete shards; merge_sweeps.py accepts only a complete pass."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from TESTS.inventory.catalog import ROOT


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'tools/go-migration/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sweep = load('sweep')
merge_sweeps = load('merge_sweeps')
JOBS = [('http', f'family-{number:02d}') for number in range(10)] + [('process', 'a_process'), ('process', 'b_process')]
PASS = {'ok': True, 'rc': 0, 'seconds': 1, 'last': '{"success": true}'}
FAIL = {'ok': False, 'rc': 1, 'seconds': 1, 'last': '{"success": false}'}


def shard_summary(index, count, results=None, head='abc'):
    expected = [sweep.key(job) for job in sweep.shard(JOBS, index, count)]
    return sweep.summary(head, 'L', expected, {name: PASS for name in expected} if results is None else results, (index, count))


class SweepTest(unittest.TestCase):
    def test_shards_are_disjoint_complete_and_stable(self):
        for count in (1, 2, 5, 12, 16):
            parts = [sweep.shard(JOBS, index, count) for index in range(1, count + 1)]
            self.assertEqual(sorted(job for part in parts for job in part), sorted(JOBS))
            self.assertEqual(parts, [sweep.shard(list(reversed(JOBS)), index, count) for index in range(1, count + 1)])
        self.assertEqual(sweep.shard(JOBS, 13, 16), [])

    def test_plan_never_emits_an_empty_shard(self):
        self.assertEqual(sweep.plan(JOBS[:3], 16), {'shards': 3, 'matrix': {'shard': [1, 2, 3]}, 'jobs': 3})
        self.assertEqual(sweep.plan(JOBS, 2)['matrix'], {'shard': [1, 2]})
        with self.assertRaises(sweep.SweepError):
            sweep.plan(JOBS, 0)

    def test_shard_argument_is_validated(self):
        self.assertEqual(sweep.parse_shard('3/16'), (3, 16))
        for bad in ('', '0/2', '3/2', '1', 'a/b'):
            with self.assertRaises(sweep.SweepError):
                sweep.parse_shard(bad)

    def test_result_is_read_from_the_last_stdout_line(self):
        self.assertEqual(sweep.judge(0, 'noise\n{"success": true}\n\n'), (True, '{"success": true}'))
        self.assertFalse(sweep.judge(1, '{"success": true}')[0])
        self.assertFalse(sweep.judge(0, '{"success": true}\ntrailing')[0])
        self.assertFalse(sweep.judge(0, '')[0])

    def test_discovery_refuses_empty_and_unknown_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'TESTS/fixtures').mkdir(parents=True)
            (root / 'TESTS/adapters/go').mkdir(parents=True)
            with self.assertRaises(sweep.SweepError):
                sweep.discover(root)
            (root / 'TESTS/fixtures/audit_log_summary_http.json').write_text('{}')
            (root / 'TESTS/adapters/go/beat_process.py').write_text("if __name__ == '__main__':\n    pass\n")
            (root / 'TESTS/adapters/go/helper_process.py').write_text('pass\n')
            self.assertEqual(sweep.discover(root), [('http', 'audit-log-summary'), ('process', 'beat_process')])
            self.assertEqual(sweep.discover(root, families=['audit-log-summary']), [('http', 'audit-log-summary')])
            for families in ([], ['absent']):
                with self.assertRaises(sweep.SweepError):
                    sweep.discover(root, families=families)
            with self.assertRaises(sweep.SweepError):
                sweep.discover(root, only='process', families=['audit-log-summary'])

    def test_merge_joins_complete_passing_shards(self):
        merged, problems = merge_sweeps.merge([shard_summary(index, 3) for index in (2, 3, 1)])
        self.assertEqual(problems, [])
        self.assertEqual((merged['head'], merged['label'], merged['total'], merged['passed']), ('abc', 'L', 12, 12))
        self.assertEqual(sorted(merged['results']), sorted(sweep.key(job) for job in JOBS))
        self.assertNotIn('shard', merged)

    def test_merge_reports_absent_shards_missing_jobs_failures_and_mixed_commits(self):
        self.assertEqual(merge_sweeps.merge([])[1], ['no shard summaries to merge'])
        _, problems = merge_sweeps.merge([shard_summary(1, 3), shard_summary(3, 3)])
        self.assertEqual(problems, ['shard 2/3 has no summary'])
        first = sweep.key(sweep.shard(JOBS, 1, 2)[0])
        merged, problems = merge_sweeps.merge([shard_summary(1, 2, {}), shard_summary(2, 2)])
        self.assertIn(f'{first} has no result', problems)
        self.assertEqual((merged['total'], merged['passed']), (12, 6))
        failing = {sweep.key(job): PASS for job in sweep.shard(JOBS, 1, 2)} | {first: FAIL}
        _, problems = merge_sweeps.merge([shard_summary(1, 2, failing), shard_summary(2, 2)])
        self.assertEqual(problems, [f'{first} failed (rc 1): {FAIL["last"]}'])
        _, problems = merge_sweeps.merge([shard_summary(1, 2), shard_summary(2, 2, head='def')])
        self.assertEqual(problems, ['shards ran on different or unknown commits: abc, def'])
        _, problems = merge_sweeps.merge([shard_summary(1, 2), shard_summary(1, 2), shard_summary(2, 2)])
        self.assertIn('shard 1/2 appears more than once', problems)


if __name__ == '__main__':
    unittest.main()
