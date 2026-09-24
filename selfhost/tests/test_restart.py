"""Restart contracts: existing containers only, durable handoff, shared leases."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from journal import Journal, Conflict
from restart import Restart, execute_restart, launch_restart, reconcile_restarts, helper_running, service_order
from server import validate_restart_request


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = self.root / '.env'
        self.env.write_text('COMPOSE_PROJECT_NAME=isolated\nASSOZETA_VERSION=edge\nAPP_URL=http://fixture.test\n')
        self.journal = Journal(self.root / '.updater/operations.sqlite3')
        self.request = validate_restart_request({'request_id': str(uuid4()), 'actor_id': str(uuid4())}, self.env)
        self.operation = self.journal.create(self.request)

    def test_restart_idempotency_and_mutual_exclusion_with_updates(self):
        self.assertEqual(self.journal.create(self.request)['id'], self.operation['id'])
        self.assertEqual(self.request['tag'], 'edge')
        for other in ({**self.request, 'request_id': str(uuid4())},
                      {**self.request, 'actor_id': str(uuid4())},
                      {**self.request, 'kind': 'update', 'release_id': 2, 'tag': 'v1.0.1'}):
            with self.assertRaises(Conflict):
                self.journal.create(other)
        self.journal.update(self.operation['id'], status='failed')
        self.journal.create({**self.request, 'kind': 'update', 'request_id': str(uuid4())})
        with self.assertRaises(Conflict):
            self.journal.create({**self.request, 'request_id': str(uuid4())})

    def test_restart_request_does_not_accept_commands_paths_or_images(self):
        for extra in ({'command': 'id'}, {'root': '/tmp'}, {'image': 'other'}, {'actor_id': 'owner'}):
            with self.assertRaises((ValueError, TypeError)):
                validate_restart_request({k: self.request[k] for k in ('request_id', 'actor_id')} | extra, self.env)

    def test_runner_reboot_preserves_live_restart_and_detects_dead_helper(self):
        self.journal.update(self.operation['id'], status='running', stage='restarting')
        self.journal.interrupt_running()
        with patch('restart.helper_running', return_value=True):
            reconcile_restarts(self.journal)
            self.assertEqual(self.journal.records()[0]['status'], 'running')
        with patch('restart.helper_running', return_value=None):
            reconcile_restarts(self.journal)
            self.assertEqual(self.journal.records()[0]['status'], 'running')
        with patch('restart.helper_running', return_value=False):
            reconcile_restarts(self.journal)
            self.assertEqual(self.journal.records()[0]['status'], 'failed')

    def test_helper_liveness_distinguishes_docker_failure_from_absence(self):
        for response, expected in (('', False), ('{"State":"exited"}', False), ('{"State":"running"}', True)):
            with patch('restart.subprocess.check_output', return_value=response):
                self.assertIs(helper_running(self.operation), expected)
        with patch('restart.subprocess.check_output', side_effect=subprocess.TimeoutExpired('docker', 10)):
            self.assertIsNone(helper_running(self.operation))

    def test_detached_helper_uses_installed_image_and_scoped_mounts_without_pull(self):
        with patch('restart.subprocess.check_output', return_value='controller-id'), \
                patch('restart.inspect_container', return_value={'Image': 'sha256:installed'}), \
                patch('restart.subprocess.run') as run:
            launch_restart(self.root, self.env, self.journal, self.operation)
        args = run.call_args.args[0]
        self.assertIn('--pull=never', args)
        self.assertIn('--detach', args)
        self.assertIn('sha256:installed', args)
        self.assertIn(f'org.assozeta.lifecycle.installation={self.root}', args)
        self.assertEqual(args[-4:], ['/runner/restart.py', str(self.root), str(self.env), self.operation['id']])
        self.assertEqual(self.journal.records()[0]['status'], 'running')

    def test_dependencies_start_first_and_tools_are_excluded(self):
        self.assertEqual(service_order({'web': {'depends_on': {'api': {}}}, 'migrate': {'profiles': ['tools']},
                                       'api': {'depends_on': {'postgres': {}}}, 'postgres': {}}), ['postgres', 'api', 'web'])

    def test_inventory_refuses_foreign_containers(self):
        restart = Restart(self.root, self.env, self.journal, self.operation['id'])
        config = {'name': 'isolated', 'services': {'api': {}}}
        foreign = {'Config': {'Labels': {'com.docker.compose.project': 'unrelated', 'com.docker.compose.service': 'api'}}}
        with patch.object(restart, 'run', side_effect=[json.dumps(config), 'foreign', json.dumps([foreign])]):
            with self.assertRaisesRegex(RuntimeError, 'does not belong'):
                restart.inventory()

    def test_real_lifecycle_and_restore_leases_block_restart_before_mutation(self):
        for relative in ('.lifecycle.flock', '.operations/lifecycle.lock'):
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            with path.open('a') as lock, patch.object(Restart, 'inventory') as inventory:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    Restart(self.root, self.env, self.journal, self.operation['id']).execute()
                inventory.assert_not_called()

    def test_ordered_stop_start_and_updater_restart_finish_only_after_health(self):
        restart = Restart(self.root, self.env, self.journal, self.operation['id'])
        application = [{'Id': name} for name in ('postgres', 'api', 'worker', 'web')]
        updater = [{'Id': 'updater'}]
        commands = []
        with patch.object(restart, 'inventory', side_effect=[application, updater]), \
                patch.object(restart, 'run', side_effect=lambda args, **kwargs: commands.append(args) or ''), \
                patch.object(restart, 'wait_healthy') as healthy:
            restart.execute()
        self.assertEqual(commands[:4], [['docker', 'stop', '--time', '60', name] for name in ('web', 'worker', 'api', 'postgres')])
        self.assertEqual(commands[4:8], [['docker', 'start', name] for name in ('postgres', 'api', 'worker', 'web')])
        self.assertEqual(commands[8], ['docker', 'restart', '--time', '30', 'updater'])
        self.assertEqual(healthy.call_count, 10)
        result = self.journal.records()[0]
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['verified_at'])

    def test_health_does_not_accept_unchanged_start_time_or_changed_image(self):
        restart = Restart(self.root, self.env, self.journal, self.operation['id'])
        original = {'Id': 'api', 'Image': 'old-image', 'State': {'StartedAt': 'before'}}
        with patch.object(restart, 'run', return_value=json.dumps([{'Image': 'new-image', 'State': {}}])):
            with self.assertRaisesRegex(RuntimeError, 'image changed'):
                restart.wait_healthy(original)
        unchanged = {**original, 'State': {'StartedAt': 'before', 'Running': True}}
        with patch.object(restart, 'run', return_value=json.dumps([unchanged])), patch('restart.time.monotonic', return_value=restart.deadline + 1):
            with self.assertRaises(TimeoutError):
                restart.wait_healthy(original)

    def test_failure_is_durable_and_public_error_does_not_leak_private_logs(self):
        with patch.object(Restart, 'execute', side_effect=TimeoutError('private-server-secret')):
            execute_restart(self.root, self.env, self.operation['id'])
        result = self.journal.records()[0]
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('private-server-secret', json.dumps(result))
        self.assertIn('log', result['recovery'])


if __name__ == '__main__':
    unittest.main()
