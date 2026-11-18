"""Partial model-hook runs can certify the full suite only after all shards are combined."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from TESTS.adapters.go import model_hooks_process as hooks


class ModelHookShards(unittest.TestCase):
    def test_ci_distributes_the_two_longest_native_families(self):
        shards = [hooks.shard_families(i, 4) for i in range(1, 5)]
        self.assertCountEqual([name for shard in shards for name in shard], hooks.FAMILIES.keys())
        self.assertFalse(any('payment-state' in shard and 'subscription-create' in shard for shard in shards))

        for count in (1, 3, len(hooks.FAMILIES)):
            with self.subTest(count=count):
                parts = [hooks.shard_families(i, count) for i in range(1, count + 1)]
                self.assertCountEqual([name for part in parts for name in part], hooks.FAMILIES.keys())
                self.assertLessEqual(max(map(len, parts)) - min(map(len, parts)), 1)

    def test_shards_preserve_full_coverage_gates(self):
        families = {'one': {'Payment'}, 'two': {'Subscription'}, 'three': {'Payment'}}
        def run_family(name, output):
            records = [{'model': next(iter(families[name])), 'outcome': 'raised.ValueError' if name == 'two' else 'saved',
                        'labels': [name], 'case': name, 'label': 'django-native'}]
            return {'success': True, 'saves': {records[0]['model']: 1}}, records

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            for key, value in dict(REPORTS=Path(tmp), FAMILIES=families, REQUIRED=['one', 'three'],
                                   REQUIRED_RAISED=['two'], UNREACHABLE={}).items():
                stack.enter_context(patch.object(hooks, key, value))
            stack.enter_context(patch.object(hooks, 'run_family', side_effect=run_family))
            stack.enter_context(patch.object(hooks, 'inventory', return_value={'writers': {}, 'problems': []}))
            stack.enter_context(patch('TESTS.adapters.django.production_runtime.require_production_python'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            paths = []
            for part in (1, 2):
                label = f'part-{part}'
                with patch.dict(os.environ, MODEL_HOOKS_SHARD=f'{part}/2'):
                    self.assertEqual(hooks.main(['--label', label]), 0)
                path = Path(tmp, 'model-hooks-' + label, 'results.json')
                self.assertFalse(json.loads(path.read_text())['success'])
                paths.append(str(path))

            def merge(label, paths):
                return hooks.main(['--label', label, '--merge-shards', *paths])

            self.assertEqual(merge('full', paths), 0)
            full = json.loads(Path(tmp, 'model-hooks-full/results.json').read_text())
            self.assertTrue(full['success'])
            self.assertEqual(set(full['families']), families.keys())
            self.assertEqual(full['required_raised']['two'], ['two:two'])
            self.assertNotEqual(merge('missing', paths[:1]), 0)
            self.assertNotEqual(merge('duplicate', [paths[0], paths[0]]), 0)
            # An individually green shard cannot hide a missing global branch.
            with patch.object(hooks, 'REQUIRED', ['unobserved']):
                self.assertNotEqual(merge('missing-branch', paths), 0)
            self.assertNotEqual(hooks.main(['--label', 'partial', '--families', 'one']), 0)
