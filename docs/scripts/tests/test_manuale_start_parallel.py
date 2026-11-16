"""Offline checks for overlapping the frontend compile in `manuale.start`.

No Docker: Compose/management commands are recorded instead of executed. The
buffered compile log is exercised for real, either with a tiny local command or
with a fake subprocess that blocks until the fake migrate has run.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'manuale.py'
spec = importlib.util.spec_from_file_location('manuale_start_parallel', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

COMPILE = 'Compile the real frontend before browser verification'
VITE = ['compose', 'run', '--rm', '--no-deps', 'ui', 'npm', 'exec', 'vite', 'build']
DEPENDENCIES = [
    'Validate isolated service configuration',
    'Build run-specific application images',
    'Start run-owned dependencies',
    'Initialize isolated object storage',
    'Migrate isolated database',
    'Seed fictional association and private browser session',
    'Retain the public fixture fingerprint before credential cleanup',
]
STARTED = 'Start application and run-owned worker for real receipt/PDF processing'


class StartParallelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.application = self.root / 'application'
        (self.application / 'UI/dist/public').mkdir(parents=True)
        (self.application / 'UI/dist/public/index.html').write_text('built\n')
        self.state = {
            'run': str(self.run), 'application': str(self.application), 'manual': str(self.root / 'manual'),
            'reference_date': '2026-01-01', 'origin': 'http://127.0.0.1:1',
            'tooling_hashes': {'docs/scripts/manuale-repeatability.py': 'sha'},
            'cache': {'decisions': []},
        }
        self.commands = []
        self.failures = {}
        self.hooks = {}
        self.compile_command = VITE

    def fake_execute(self, command, run, label, *, cwd=None, env=None, input=None):
        self.commands.append((label, list(command)))
        hook = self.hooks.get(label)
        if hook is not None:
            hook()
        if label in self.failures:
            raise self.failures[label]

    def fake_compose(self, state, *args):
        return list(self.compile_command) if 'vite' in args else ['compose', *args]

    def fake_management(self, state, *args, reuse=False):
        return ['manage', *args]

    def labels(self):
        return [label for label, _ in self.commands]

    def command_for(self, label):
        return [command for name, command in self.commands if name == label][0]

    def run_start(self, *, parallel=True):
        environment = {'MANUALE_PARALLEL_FRONTEND': '1' if parallel else '0'}
        patches = [
            patch.dict(os.environ, environment),
            patch.object(runner, 'configure_runtime', lambda state: None),
            patch.object(runner, 'execute', self.fake_execute),
            patch.object(runner, 'compose', self.fake_compose),
            patch.object(runner, 'management', self.fake_management),
            patch.object(runner, 'ui_sources', lambda application: {'UI/index.html': 'sha'}),
            patch.object(runner, 'sha', lambda path: 'sha'),
        ]
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            for extra in self.extra_patches():
                stack.enter_context(extra)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                runner.start(self.state)
        return output.getvalue()

    def extra_patches(self):
        return []

    def compile_threads(self):
        return [thread for thread in threading.enumerate() if thread.name == 'manuale-frontend-compile']

    def timings(self):
        return json.loads((self.run / 'timings.json').read_text())['events']

    def execution_log(self):
        return (self.run / 'execution.log').read_text()

    def test_serial_mode_keeps_the_historical_command_order(self):
        self.run_start(parallel=False)
        self.assertEqual(self.labels(), DEPENDENCIES + [COMPILE, STARTED])
        self.assertEqual(self.command_for(COMPILE), VITE)
        self.assertEqual(self.compile_threads(), [])
        self.assertFalse((self.run / 'timings.json').exists())

    def test_parallel_mode_overlaps_the_compile_with_migrate(self):
        compile_started, migrate_done, order = threading.Event(), threading.Event(), []

        def fake_run(command, cwd=None, env=None, stdout=None, stderr=None, input=None):
            self.assertEqual(list(command), VITE)
            if migrate_done.is_set():
                raise AssertionError('the compile started after migrate finished')
            order.append('compile-start')
            stdout.write(b'vite build output\n')
            compile_started.set()
            if not migrate_done.wait(5):
                raise AssertionError('migrate never ran while the compile was in flight')
            order.append('compile-end')
            return SimpleNamespace(returncode=0)

        def migrate():
            if not compile_started.wait(5):
                raise AssertionError('the compile did not start before migrate')
            order.append('migrate-start')
            order.append('migrate-end')
            migrate_done.set()

        self.hooks['Start run-owned dependencies'] = lambda: order.append('deps-start')
        self.hooks['Migrate isolated database'] = migrate
        self.hooks[STARTED] = lambda: order.append('api-up')
        with patch.object(subprocess, 'run', fake_run):
            output = self.run_start()

        self.assertEqual(self.labels(), DEPENDENCIES + [STARTED])
        # The compile thread may be scheduled before or during the dependency
        # start; it must still be in flight when migrate runs and end before api.
        self.assertEqual(sorted(order), ['api-up', 'compile-end', 'compile-start', 'deps-start',
                                         'migrate-end', 'migrate-start'])
        self.assertLess(order.index('compile-start'), order.index('migrate-end'))
        self.assertLess(order.index('deps-start'), order.index('migrate-end'))
        # Compose creates the project network in 'up'; the compile must not race it.
        self.assertLess(order.index('deps-start'), order.index('compile-start'))
        self.assertLess(order.index('compile-end'), order.index('api-up'))
        self.assertLess(order.index('migrate-end'), order.index('api-up'))
        self.assertEqual(output.count(COMPILE), 1)
        self.assertEqual(self.compile_threads(), [])

    def test_compile_failure_raises_after_the_main_sequence(self):
        self.compile_command = [sys.executable, '-c', 'print("compile boom"); raise SystemExit(7)']
        with self.assertRaisesRegex(RuntimeError, rf'{COMPILE} failed \(7\); inspect .*execution\.log'):
            self.run_start()
        self.assertNotIn(STARTED, self.labels())
        self.assertIn('Migrate isolated database', self.labels())
        self.assertEqual(self.compile_threads(), [])
        log = self.execution_log()
        self.assertEqual(log.count(COMPILE), 1)
        self.assertIn('compile boom', log)
        self.assertEqual([event['status'] for event in self.timings()], ['failed'])
        self.assertFalse(list(self.run.glob('.buffered-*.log')))

    def test_main_failure_wins_and_joins_the_compile(self):
        self.compile_command = [sys.executable, '-c', 'import time; time.sleep(0.5); print("late compile")']
        self.failures['Migrate isolated database'] = RuntimeError('migrate boom')
        with self.assertRaisesRegex(RuntimeError, '^migrate boom$'):
            self.run_start()
        self.assertNotIn(STARTED, self.labels())
        self.assertEqual(self.compile_threads(), [])
        # The published log proves start waited for the compile before raising.
        self.assertIn('late compile', self.execution_log())
        self.assertEqual([(event['name'], event['status']) for event in self.timings()], [(COMPILE, 'passed')])

    def test_reused_frontend_runs_no_compile_and_no_thread(self):
        before = set(threading.enumerate())
        self.state['reuse_frontend'] = str(self.root / 'previous')
        retained = {'source_run': 'previous', 'sources': {'UI/index.html': 'sha'}, 'artifacts': {}}
        reused = []

        def reuse(state):
            reused.append(state['reuse_frontend'])
            return retained

        with patch.object(runner, 'reuse_frontend', reuse):
            self.run_start()
        self.assertEqual(reused, [self.state['reuse_frontend']])
        self.assertNotIn(COMPILE, self.labels())
        self.assertEqual(self.compile_threads(), [])
        self.assertEqual(set(threading.enumerate()) - before, set())
        self.assertEqual(json.loads((self.run / 'frontend-build.json').read_text()), retained)

    def test_compile_log_and_timing_are_published_exactly_once(self):
        self.compile_command = [sys.executable, '-c', 'print("vite ok")']
        self.run_start()
        log = self.execution_log()
        self.assertEqual(log.count(COMPILE), 1)
        self.assertEqual(log.count('vite ok'), 1)
        events = self.timings()
        self.assertEqual([event['name'] for event in events], [COMPILE])
        self.assertEqual(events[0]['kind'], 'command')
        self.assertEqual(events[0]['status'], 'passed')
        self.assertIsInstance(events[0]['elapsed_seconds'], float)
        self.assertFalse(list(self.run.glob('.buffered-*.log')))


if __name__ == '__main__':
    unittest.main()
