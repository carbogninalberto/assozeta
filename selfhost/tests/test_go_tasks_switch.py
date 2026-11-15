"""Owner switch for the opt-in Go background task consumer; no Docker is used."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from common import read_env
from go_tasks import MISSING_WORKER, apply_go_tasks, release_has_go_worker, validate_go_tasks_request
from journal import Journal, Conflict


class GoTasksSwitchTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_GO_TASKS=0\nGO_TASK_QUEUE_TASKS=\nASSOZETA_VERSION=edge\n')
        self.journal = Journal(self.root / '.updater/operations.sqlite3')

    def request(self, enabled):
        return validate_go_tasks_request(
            {'request_id': str(uuid4()), 'actor_id': str(uuid4()), 'enabled': enabled}, self.env)

    def test_request_carries_no_task_names_and_requires_a_real_boolean(self):
        valid = {'request_id': str(uuid4()), 'actor_id': str(uuid4()), 'enabled': True}
        request = validate_go_tasks_request(valid, self.env)
        self.assertEqual((request['kind'], request['tag'], request['release_id']), ('go_tasks', 'edge', None))
        for extra in ({'tasks': 'x'}, {'task_names': ['core.tasks.send_mail_async']}):
            with self.assertRaises(ValueError):
                validate_go_tasks_request({**valid, **extra}, self.env)
        for enabled in (1, 0, 'true', None):
            with self.assertRaises(ValueError):
                validate_go_tasks_request({**valid, 'enabled': enabled}, self.env)
        for field in ('request_id', 'actor_id'):
            with self.assertRaises(ValueError):
                validate_go_tasks_request({**valid, field: 'owner'}, self.env)

    def test_enabling_writes_the_fixed_task_list_and_restarts_through_the_cli(self):
        operation = self.journal.create(self.request(True))
        with patch('go_tasks.release_has_go_worker', return_value=True), \
                patch('go_tasks.subprocess.run') as run:
            apply_go_tasks(self.root, self.env, self.journal, operation)
        values = read_env(self.env)
        self.assertEqual(values['ASSOZETA_GO_TASKS'], '1')
        self.assertEqual(values['GO_TASK_QUEUE_TASKS'], 'core.tasks.send_mail_async')
        self.assertEqual(run.call_args.args[0], [str(self.root / 'bin/assozeta'), 'restart'])
        self.assertEqual(run.call_args.kwargs['env']['ASSOZETA_ENV_FILE'], str(self.env))
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['stage']), ('succeeded', 'completed'))
        self.assertTrue(record['verified_at'])

    def test_disabling_empties_the_list_but_keeps_the_consumer_for_the_queue(self):
        self.env.write_text('ASSOZETA_GO_TASKS=1\nGO_TASK_QUEUE_TASKS=core.tasks.send_mail_async\nASSOZETA_VERSION=edge\n')
        operation = self.journal.create(self.request(False))
        with patch('go_tasks.release_has_go_worker') as check, patch('go_tasks.subprocess.run'):
            apply_go_tasks(self.root, self.env, self.journal, operation)
        check.assert_not_called()
        values = read_env(self.env)
        self.assertEqual(values['ASSOZETA_GO_TASKS'], '1')
        self.assertEqual(values['GO_TASK_QUEUE_TASKS'], '')
        self.assertEqual(self.journal.records()[0]['status'], 'succeeded')

    def test_enabling_is_refused_before_any_change_without_the_worker_service(self):
        original = self.env.read_text()
        operation = self.journal.create(self.request(True))
        with patch('go_tasks.release_has_go_worker', return_value=False), \
                patch('go_tasks.subprocess.run') as run:
            apply_go_tasks(self.root, self.env, self.journal, operation)
        self.assertEqual(self.env.read_text(), original)
        run.assert_not_called()
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['error']), ('failed', MISSING_WORKER))

    def test_worker_detection_reads_the_go_tasks_profile_services(self):
        with patch('go_tasks.subprocess.check_output', return_value='api\nworker-go\n') as output:
            self.assertTrue(release_has_go_worker(self.root, self.env))
        command = output.call_args.args[0]
        self.assertIn('--profile', command)
        self.assertEqual(command[-2:], ['config', '--services'])
        with patch('go_tasks.subprocess.check_output', return_value='api\nworker-go-beta\n'):
            self.assertFalse(release_has_go_worker(self.root, self.env))

    def test_interrupted_switch_fails_without_requiring_recovery(self):
        operation = self.journal.create(self.request(True))
        self.journal.update(operation['id'], status='running', stage='restarting')
        self.journal.interrupt_running()
        record = self.journal.records()[0]
        self.assertEqual((record['status'], record['stage']), ('failed', 'failed'))
        self.assertIn('./bin/assozeta restart', record['recovery'])
        self.assertFalse(self.journal.requiring_recovery())

    def test_repeated_request_is_idempotent_and_a_changed_switch_conflicts(self):
        request = self.request(True)
        operation = self.journal.create(request)
        self.assertEqual(self.journal.create(request)['id'], operation['id'])
        for other in ({**request, 'request_id': str(uuid4())}, {**request, 'enabled': False}):
            with self.assertRaises(Conflict):
                self.journal.create(other)


if __name__ == '__main__':
    unittest.main()
