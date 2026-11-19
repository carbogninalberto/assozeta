"""Check the inventory against the Celery app and the CI harness matrix."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / 'docs/architecture/go-migration/go-tasks-inventory.json'


class GoBackgroundTasks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A fresh interpreter runs this module in CI, with disposable settings.
        import environ
        import django
        sys.path.insert(0, str(ROOT / 'BE'))
        cls.addClassCleanup(sys.path.remove, str(ROOT / 'BE'))
        with tempfile.TemporaryDirectory() as work, patch.dict(os.environ):
            environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
            os.environ.update(DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings',
                              TESTS_DATABASE='assozeta_contract_inventory', TESTS_WORKDIR=work,
                              DBTYPE='POSTGRES', DBHOST=work, USE_PGBOUNCER='False',
                              JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='fixture',
                              JWT_PUBLIC_KEY='fixture')
            with patch('environ.Env.read_env'), contextlib.redirect_stdout(io.StringIO()):
                django.setup()
                from core.celery import app
                app.loader.import_default_modules()
                from django_celery_beat.schedulers import DatabaseScheduler
                defaults = {}
                scheduler = DatabaseScheduler.__new__(DatabaseScheduler)
                scheduler.app = app
                with patch.object(scheduler, 'update_from_dict', side_effect=defaults.update):
                    scheduler.install_default_entries({})
                cls.scheduled = {e['task'] for e in (*app.conf.beat_schedule.values(), *defaults.values())}
                cls.registered = {name for name in app.tasks if not name.startswith('celery.') or name in cls.scheduled}

    def test_django_authority(self):
        self.assertIn('celery.backend_cleanup', self.scheduled)
        self.assertIn('instance.restore.execute', self.registered)
        self.assertIn('core.tasks.send_mail_async', self.registered)
        self.assertTrue(self.scheduled <= self.registered)

    def test_inventory_matches_running_django(self):
        rows = json.loads(INVENTORY.read_text())
        self.assertCountEqual([row['name'] for row in rows], self.registered | {'fixture.early_ack'})
        self.assertEqual({row['name'] for row in rows if row['scheduled']}, self.scheduled)
        fixture, = [row for row in rows if row['name'] == 'fixture.early_ack']
        self.assertEqual(fixture['excluded'], 'test fixture')

    def test_ci_runs_process_harnesses(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/go-quality.yml').read_text())['jobs']
        job = jobs['go-background-tasks']
        command = 'python -m "TESTS.adapters.go.$HARNESS"'
        runs = [step for step in job['steps'] if any(line.strip().startswith(command)
                for line in step.get('run', '').splitlines())]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['env']['HARNESS'], '${{ matrix.harness }}')
        harnesses = job['strategy']['matrix']['harness']
        self.assertCountEqual(harnesses, set(harnesses))
        files = {p.stem for p in (ROOT / 'TESTS/adapters/go').glob('*_process.py')}
        # message_image_process is a helper, executed by its ProcessControls unit suite.
        from TESTS.adapters.go import message_image_process, test_message_image_process
        self.assertIs(test_message_image_process.process, message_image_process)
        self.assertEqual(set(harnesses) | {'message_image_process'}, files | {'schedule_due', 'schedule_page'})
        self.assertIn('go-background-tasks', jobs['golang']['needs'])

    def test_inventory_pr_job_has_the_project_toolchain(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/pull-request-quality.yml').read_text())['jobs']
        steps = jobs['go-tasks-inventory']['steps']
        setup = [s for s in steps if s.get('uses', '').startswith('actions/setup-go@')
                 and s.get('with', {}).get('go-version-file') == 'BE_GO/go.mod']
        self.assertTrue(setup)
        self.assertTrue(any('go_tasks_inventory.py --check' in s.get('run', '') for s in steps))
        self.assertIn('go-tasks-inventory', jobs['result']['needs'])

    def test_ci_collects_all_model_hook_shards(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/go-quality.yml').read_text())['jobs']
        matrix = jobs['go-background-tasks']['strategy']['matrix']
        parts = list(matrix['part']) + [row['part'] for row in matrix['include']
                                       if row['harness'] == 'model_hooks_process']
        self.assertCountEqual(parts, [1, 2, 3, 4])
        merge = jobs['go-model-hook-coverage']
        self.assertIn('go-background-tasks', merge['needs'])
        self.assertTrue(any('--merge-shards' in step.get('run', '') for step in merge['steps']))
        self.assertIn('go-model-hook-coverage', jobs['golang']['needs'])

    def test_changes_to_pr_quality_run_the_full_suite(self):
        workflow = '.github/workflows/pull-request-quality.yml'
        jobs = yaml.safe_load((ROOT / workflow).read_text())['jobs']
        selector, = (step for step in jobs['changes']['steps'] if step.get('id') == 'filter')
        filters = yaml.safe_load(selector['with']['filters'])
        for area in ('golang', 'backend', 'ui', 'selfhost', 'stack'):
            with self.subTest(area=area):
                paths = [path for pattern in filters[area] if not pattern.startswith('!')
                         for path in pattern.strip('{}').split(',')]
                self.assertIn(workflow, paths)

    def test_ci_reports_the_failing_source_line_without_fixture_data(self):
        jobs = yaml.safe_load((ROOT / '.github/workflows/go-quality.yml').read_text())['jobs']
        steps = jobs['go-background-tasks']['steps']
        run, = (s for s in steps if '-m "TESTS.adapters.go.$HARNESS"' in s.get('run', ''))
        self.assertTrue(run.get('id'))
        diagnostic, = (s for s in steps if 'failure()' in s.get('if', '')
                       and f"steps.{run['id']}.outcome == 'failure'" in s['if'])
        harness = 'TESTS/adapters/go/celery_consumer_import_process.py'
        with tempfile.TemporaryDirectory() as work:
            Path(work, 'go-background-task.log').write_text(
                f'PASS: fixture-only-value\nTraceback (most recent call last):\n'
                f'  File "{ROOT / harness}", line 811, in fixture_only_function\n'
                f'  File "{ROOT}/quality-reports/fixture-only-file.py", line 8, in call\n'
                f'  File "{work}/fixture-only-file.py", line 12, in call\n'
                '    print("fixture-only-source")\nAssertionError: fixture-only-exception\n')
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', diagnostic['run']],
                                    cwd=ROOT, env={**os.environ, 'RUNNER_TEMP': work, 'GITHUB_WORKSPACE': str(ROOT)},
                                    capture_output=True, text=True, check=True)
        self.assertIn(f'{harness}:811', result.stdout)
        self.assertNotIn('fixture', result.stdout + result.stderr)
        self.assertNotIn(str(ROOT), result.stdout + result.stderr)

    def test_ci_import_difference_reports_only_known_sections(self):
        steps = yaml.safe_load((ROOT / '.github/workflows/go-quality.yml').read_text())['jobs']['go-background-tasks']['steps']
        diagnostic = next(step for step in steps if 'traceback locations' in step.get('name', '').lower())
        with tempfile.TemporaryDirectory() as work:
            subprocess.run(['git', 'init', '-q', work], check=True)
            Path(work, 'go-background-task.log').touch()
            report = Path(work, 'quality-reports/go-migration/consumer-import-process/difference.json')
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({
                'scenario': 'private-case', 'django': {'audit': ['private-a'], 'models': {}, 'private-key': 0},
                'go': {'audit': ['private-b'], 'models': {}, 'private-key': 1},
            }))
            env = {**os.environ, 'GITHUB_WORKSPACE': work, 'RUNNER_TEMP': work,
                   'HARNESS': 'celery_consumer_import_process'}
            output = subprocess.check_output(['bash', '-euo', 'pipefail', '-c', diagnostic['run']],
                                             cwd=work, env=env, text=True)
        self.assertIn('audit', output)
        self.assertNotIn('models', output)
        self.assertNotIn('private', output)


if __name__ == '__main__':
    unittest.main()
