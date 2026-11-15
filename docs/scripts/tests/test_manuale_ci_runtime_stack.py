"""The runtime phase owns a stack when the capture phase never started one."""
import importlib.util
import json
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_') + '_under_test',
                                                  ROOT / 'docs/scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


ci = module('manuale-ci')


class RuntimeStackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / 'run'
        self.run.mkdir()

    def write(self, state, *, plan=True):
        (self.run / 'run.json').write_text(json.dumps(state))
        path = self.run / 'ci-plan.json'
        if plan:
            path.write_text(json.dumps({'mode': 'full', 'fresh_workflows': ['alpha'], 'reused_workflows': []}))
        else:
            path.unlink(missing_ok=True)

    def phase(self, name, state, *, plan=True):
        """Run one phase against a fake runner that records the calls it receives."""
        self.write(state, plan=plan)
        calls = []

        @contextmanager
        def timed(target, stage):
            yield

        def record(call, persist=False):
            def run(state):
                calls.append(call)
                if persist:
                    # The real start configures the private stack and writes the
                    # run state, so the later cleanup step finds it on disk.
                    state['env_file'] = str(self.root / '.env.dev')
                    (self.run / 'run.json').write_text(json.dumps(state))
            return run

        runner = SimpleNamespace(timed=timed, index=record('index'), start=record('start', persist=True),
                                 evaluate=record('evaluate'), export=record('export'),
                                 cleanup=record('cleanup'))
        args = SimpleNamespace(run=str(self.run), phase=name)
        with patch.dict(os.environ, {'MANUALE_TIMING_FILE': '', 'MANUALE_BUILDX_CACHE': ''}):
            ci.phase(args, runner)
        return calls

    def test_a_legacy_capture_stack_is_never_started_again(self):
        state = {'run': str(self.run), 'env_file': str(self.root / '.env.dev'),
                 'selected_recipes': ['alpha']}
        self.assertEqual(self.phase('runtime', state), ['index', 'evaluate', 'export'])
        self.assertEqual(self.phase('cleanup', state), ['cleanup'])

    def test_a_distributed_merge_starts_exactly_one_stack_for_runtime(self):
        state = {'run': str(self.run), 'status': 'captured', 'capture_mode': 'distributed',
                 'selected_recipes': ['alpha']}
        self.assertEqual(self.phase('runtime', state), ['index', 'start', 'evaluate', 'export'])
        recorded = json.loads((self.run / 'run.json').read_text())
        self.assertTrue(recorded['env_file'])
        # The separate cleanup step reads the persisted run state, as in CI.
        self.assertEqual(self.phase('cleanup', recorded), ['cleanup'])

    def test_a_run_without_a_capture_stack_starts_one_for_runtime(self):
        state = {'run': str(self.run), 'selected_recipes': ['alpha']}
        self.assertEqual(self.phase('runtime', state), ['index', 'start', 'evaluate', 'export'])

    def test_removed_services_are_not_cleaned_up_twice(self):
        state = {'run': str(self.run), 'env_file': str(self.root / '.env.dev'), 'services_removed': True}
        self.assertEqual(self.phase('cleanup', state), [])

    def test_a_capture_shard_cleans_up_without_a_verification_plan(self):
        # capture-shard prepares a run without the verify job's plan file.
        state = {'run': str(self.run), 'env_file': str(self.root / '.env.dev'), 'capture_id': '0' * 16}
        self.assertEqual(self.phase('cleanup', state, plan=False), ['cleanup'])

    def test_every_other_phase_still_requires_the_plan(self):
        with self.assertRaises(FileNotFoundError):
            self.phase('runtime', {'run': str(self.run)}, plan=False)


if __name__ == '__main__':
    unittest.main()
