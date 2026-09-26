"""Cleanup retention and shared CLI/UI success boundary, without live Docker."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from cleanup import cleanup, cleanup_images, protected_images, references


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_VERSION=1.0.3\n')
        history = self.root / '.updater/distribution-history/100'
        history.mkdir(parents=True)
        (history / 'environment').write_text('ASSOZETA_VERSION=1.0.2\n')

    def test_retains_current_previous_and_all_container_images(self):
        def command(*args):
            if args == ('ps', '-aq'):
                return 'stopped-container'
            if args[:2] == ('container', 'inspect'):
                return json.dumps([{'Image': 'stopped-image'}])
            return json.dumps([{'Id': args[-1]}])
        with patch('cleanup.docker', side_effect=command):
            protected = protected_images(self.root, self.env)
        self.assertEqual(len(protected), 9)
        self.assertIn('stopped-image', protected)
        self.assertIn('ghcr.io/carbogninalberto/assozeta-backend:1.0.2', protected)

    def test_pending_recovery_and_missing_history_prevent_image_deletion(self):
        (self.root / '.updater/pending-distribution').mkdir()
        with patch('cleanup.docker') as docker:
            with self.assertRaises(RuntimeError):
                cleanup_images(self.root, self.env)
            docker.assert_not_called()
        (self.root / '.updater/pending-distribution').rmdir()
        (self.root / '.updater/distribution-history/100/environment').unlink()
        with patch('cleanup.docker', return_value='[{"Id":"current"}]') as docker:
            with self.assertRaises(OSError):
                cleanup_images(self.root, self.env)
            self.assertFalse(any(call.args[:2] == ('image', 'rm') for call in docker.call_args_list))

    def test_only_removes_unprotected_exclusively_assozeta_images_without_force(self):
        old = 'ghcr.io/carbogninalberto/assozeta-backend:1.0.1'
        images = {
            'old': {'RepoTags': [old]},
            'foreign': {'RepoTags': ['other/application:old']},
            'shared': {'RepoTags': [old, 'custom:keep']},
            'anonymous': {},
        }
        def command(*args):
            if args[:2] == ('image', 'ls'):
                return 'current previous old foreign shared anonymous'
            if args[:2] == ('image', 'inspect'):
                return json.dumps([images[args[-1]]])
            return ''
        with patch('cleanup.protected_images', return_value={'current', 'previous'}), \
                patch('cleanup.docker', side_effect=command) as docker:
            cleanup_images(self.root, self.env)
        removals = [call.args for call in docker.call_args_list if call.args[:2] == ('image', 'rm')]
        self.assertEqual(removals, [('image', 'rm', old)])

    def test_failures_are_nonfatal_and_cache_cleanup_is_age_limited(self):
        with patch('cleanup.cleanup_images', side_effect=RuntimeError('missing metadata')), \
                patch('cleanup.docker', side_effect=subprocess.TimeoutExpired('docker', 120)) as docker:
            cleanup(self.root, self.env)
        docker.assert_called_once_with('builder', 'prune', '--force', '--filter', 'until=168h')

    def test_immutable_references_take_precedence_over_version_tags(self):
        refs = list(references({'ASSOZETA_VERSION': 'v1.0.3', 'ASSOZETA_BACKEND_REF': 'backend@sha256:abc'}))
        self.assertEqual(refs[0], 'backend@sha256:abc')
        self.assertTrue(refs[1].endswith(':1.0.3'))

    def test_cli_and_ui_cleanup_only_after_success_and_failure_is_nonfatal(self):
        definitions = (ROOT / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]
        for runner in ('0', '1'):
            for fail_health in ('0', '1'):
                with self.subTest(runner=runner, fail_health=fail_health):
                    script = definitions + '''
supports_self_update() { return 0; }
ensure_configuration_values() { :; }
docker() { :; }
backup_prod() { archive=/backup; }
set_env_value() { :; }
prod_compose() { :; }
initialise_prod() { :; }
start_prod_app_services() { [ "$TEST_FAIL_HEALTH" = 0 ]; }
status_prod() { :; }
run_updater_tool() {
    printf 'TOOL %s\\n' "$1"
    [ "$1" != /runner/cleanup.py ]
}
upgrade_prod 1.0.4
'''
                    result = subprocess.run(['sh', '-c', script], capture_output=True, text=True, env={
                        **os.environ, 'ASSOZETA_ENV_FILE': str(self.env), 'ASSOZETA_RUNNER_ACTIVE': runner,
                        'ASSOZETA_EXPECTED_SOURCE': '1.0.3', 'TEST_FAIL_HEALTH': fail_health,
                    })
                    if fail_health == '1':
                        self.assertNotEqual(result.returncode, 0)
                        self.assertNotIn('TOOL /runner/cleanup.py', result.stdout)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout.count('TOOL /runner/cleanup.py'), 1)
                        self.assertLess(result.stdout.index('TOOL /runner/engine.py'), result.stdout.index('TOOL /runner/cleanup.py'))
                        self.assertIn('upgrade complete', result.stdout)


if __name__ == '__main__':
    unittest.main()
