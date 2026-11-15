"""Unit tests for the sharded account suite and its report merge (no database)."""
import ast
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from TESTS.adapters.django import accounts, accounts_merge

ACCOUNTS = Path(accounts.__file__)
SHARED = ('social_configuration_cases', 'schema', 'customer_data', 'checks')


def synthetic_shards():
    """Three shard reports whose union is exactly the full account report."""
    keys = sorted(accounts_merge.FULL_REPORT_KEYS - {'success'} - set(SHARED))
    size = (len(keys) + len(accounts_merge.SHARDS) - 1) // len(accounts_merge.SHARDS)
    groups = [keys[index:index + size] for index in range(0, len(keys), size)]
    shared = {'social_configuration_cases': 'django', 'schema': 'current Django models',
              'customer_data': 'none', 'checks': ['django readback unchanged']}
    reports = {}
    for shard, group in zip(accounts_merge.SHARDS, groups):
        report = {'success': True, 'shard': shard, **shared}
        report.update({key: f'{key} result' for key in group})
        reports[shard] = report
    return reports


def write_shards(root: Path, reports) -> list:
    paths = []
    for shard, report in reports.items():
        path = root / f'accounts-{shard}'
        path.mkdir(parents=True)
        (path / 'results.json').write_text(json.dumps(report) + '\n')
        paths.append(path)
    return paths


class ParserTest(unittest.TestCase):
    def parse_error(self, argv):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                accounts.build_parser().parse_args(argv)
        self.assertEqual(raised.exception.code, 2)
        return errors.getvalue()

    def test_shard_and_only_are_mutually_exclusive(self):
        self.assertIn('not allowed with argument', self.parse_error(['--shard', 'tasks', '--only', 'login-http']))
        self.assertIn('not allowed with argument', self.parse_error(['--only', 'login-http', '--shard', 'login']))

    def test_shard_and_only_parse_alone(self):
        parser = accounts.build_parser()
        for shard in accounts_merge.SHARDS:
            options = parser.parse_args(['--shard', shard])
            self.assertEqual((options.shard, options.only), (shard, None))
        self.assertEqual(parser.parse_args([]).shard, None)
        self.assertEqual(parser.parse_args(['--only', 'login-http']).only, 'login-http')

    def test_shard_rejects_unknown_name(self):
        self.assertIn('invalid choice', self.parse_error(['--shard', 'payments']))


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_happy_path(self):
        reports = synthetic_shards()
        self.assertEqual({key for report in reports.values() for key in report} - {'shard'},
                         accounts_merge.FULL_REPORT_KEYS)
        merged = accounts_merge.merge(write_shards(self.root, reports))
        self.assertTrue(merged['success'])
        self.assertEqual(merged['shards'], accounts_merge.SHARDS)
        self.assertEqual(set(merged) - {'shards'}, accounts_merge.FULL_REPORT_KEYS)
        for key in SHARED:
            self.assertEqual(merged[key], reports['tasks'][key])

    def test_accepts_results_json_paths(self):
        reports = synthetic_shards()
        paths = [path / 'results.json' for path in write_shards(self.root, reports)]
        self.assertEqual(accounts_merge.merge(paths)['shards'], accounts_merge.SHARDS)

    def test_main_writes_the_merged_report(self):
        output = self.root / 'merged/results.json'
        paths = write_shards(self.root / 'shards', synthetic_shards())
        self.assertEqual(accounts_merge.main(['--input', *map(str, paths), '--output', str(output)]), 0)
        merged = json.loads(output.read_text())
        self.assertEqual(merged['shards'], accounts_merge.SHARDS)
        self.assertTrue(merged['success'])

    def test_rejects_missing_shard(self):
        reports = synthetic_shards()
        reports.pop('identity')
        with self.assertRaisesRegex(ValueError, 'missing account shards: identity'):
            accounts_merge.merge(write_shards(self.root, reports))

    def test_rejects_failed_shard(self):
        reports = synthetic_shards()
        reports['login']['success'] = False
        with self.assertRaisesRegex(ValueError, 'login: shard did not succeed'):
            accounts_merge.merge(write_shards(self.root, reports))

    def test_main_exits_non_zero_on_violation(self):
        reports = synthetic_shards()
        reports['tasks']['success'] = False
        paths = write_shards(self.root, reports)
        output = self.root / 'merged/results.json'
        self.assertEqual(accounts_merge.main(['--input', *map(str, paths), '--output', str(output)]), 1)
        self.assertFalse(output.exists())

    def test_rejects_duplicate_shard(self):
        reports = synthetic_shards()
        paths = write_shards(self.root, reports)
        duplicate = self.root / 'accounts-tasks-copy'
        duplicate.mkdir()
        (duplicate / 'results.json').write_text(json.dumps(reports['tasks']))
        with self.assertRaisesRegex(ValueError, 'tasks: shard reported more than once'):
            accounts_merge.merge(paths + [duplicate])

    def test_rejects_extra_key(self):
        reports = synthetic_shards()
        reports['identity']['unexpected_key'] = 'value'
        with self.assertRaisesRegex(ValueError, "extra=\\['unexpected_key'\\]"):
            accounts_merge.merge(write_shards(self.root, reports))

    def test_rejects_missing_key(self):
        reports = synthetic_shards()
        key = sorted(set(reports['tasks']) - {'success', 'shard'} - set(SHARED))[0]
        reports['tasks'].pop(key)
        with self.assertRaisesRegex(ValueError, f"missing=\\['{key}'\\]"):
            accounts_merge.merge(write_shards(self.root, reports))

    def test_rejects_conflicting_shared_values(self):
        reports = synthetic_shards()
        reports['login']['schema'] = 'a different schema'
        with self.assertRaisesRegex(ValueError, 'disagree on schema'):
            accounts_merge.merge(write_shards(self.root, reports))

    def test_rejects_conflicting_duplicated_keys(self):
        reports = synthetic_shards()
        reports['login']['social_configuration_cases'] = 'other'
        reports['login']['schema'] = reports['identity']['schema']
        with self.assertRaisesRegex(ValueError, 'social_configuration_cases: account shards disagree'):
            accounts_merge.merge(write_shards(self.root, reports))

    def test_rejects_unknown_shard_and_bad_input(self):
        reports = synthetic_shards()
        reports['tasks']['shard'] = 'payments'
        with self.assertRaisesRegex(ValueError, 'not an account shard report'):
            accounts_merge.merge(write_shards(self.root, reports))
        with self.assertRaisesRegex(ValueError, 'unreadable account shard report'):
            accounts_merge.merge([self.root / 'absent'])


class ReportConstructionTest(unittest.TestCase):
    def report_literals(self):
        tree = ast.parse(ACCOUNTS.read_text())
        return tree, [node.value for node in ast.walk(tree)
                      if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                      and any(isinstance(target, ast.Name) and target.id == 'report'
                              for target in node.targets)]

    def test_full_report_keys_match_the_merge_constant(self):
        tree, literals = self.report_literals()
        literal = max(literals, key=lambda node: len(node.keys))
        keys = {key.value for key in literal.keys}
        self.assertIn('social_configuration_cases', keys)
        self.assertNotIn('shard', keys)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                        and target.value.id == 'report' and isinstance(target.slice, ast.Constant)):
                    keys.add(target.slice.value)
        self.assertEqual(keys, set(accounts_merge.FULL_REPORT_KEYS))

    def test_shard_reports_cover_the_full_report_in_three_groups(self):
        _, literals = self.report_literals()
        shards = {}
        for literal in literals:
            keys = [key.value for key in literal.keys if isinstance(key, ast.Constant)]
            if 'shard' not in keys:
                continue
            values = [value.value for key, value in zip(literal.keys, literal.values)
                      if isinstance(key, ast.Constant) and key.value == 'shard']
            self.assertEqual(len(values), 1)
            shards[values[0]] = set(keys) - {'shard'}
        self.assertEqual(sorted(shards), sorted(accounts_merge.SHARDS))
        self.assertEqual(set().union(*shards.values()), set(accounts_merge.FULL_REPORT_KEYS))
        tasks, identity, login = (shards[name] for name in accounts_merge.SHARDS)
        self.assertIn('login_http', tasks)
        self.assertIn('medical_orphan_cleanup_cases', tasks)
        self.assertIn('synthetic_accounts', identity)
        self.assertIn('refresh_http_contracts_per_backend', identity)
        self.assertNotIn('profile_images', identity)
        self.assertNotIn('login_transport_contracts_per_backend', identity)
        self.assertIn('social_configuration_cases', login)
        self.assertIn('profile_images', login)
        self.assertNotIn('refresh_http_contracts_per_backend', login)
        self.assertNotIn('schema', login)


if __name__ == '__main__':
    unittest.main()
