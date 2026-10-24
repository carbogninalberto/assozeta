"""Dual API delivery and historical recovery, without touching an installation."""
import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from engine import Engine
import recovery

DEFINITIONS = (ROOT / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]


class GoComposeTests(unittest.TestCase):
    def test_normal_compose_delivers_second_api_with_same_configuration_and_lifecycle_lock(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / '.env'
            env_file.write_text((ROOT / 'selfhost/.env.example').read_text())
            environment = {**os.environ, 'ASSOZETA_ENV_FILE': str(env_file),
                           'ASSOZETA_SELFHOST_DIR': directory}
            result = subprocess.run(['docker', 'compose', '--env-file', str(env_file), '-f',
                                     str(ROOT / 'selfhost/compose.yml'), 'config', '--format', 'json'],
                                    env=environment, capture_output=True, text=True, check=True)
            services = json.loads(result.stdout)['services']
            self.assertIn('api-go', services)
            django, golang = services['api'], services['api-go']
            self.assertEqual(golang['image'], 'ghcr.io/carbogninalberto/assozeta-backend-go:latest')
            self.assertEqual(golang['user'], '10001:10001')
            self.assertTrue(golang['read_only'])
            django_manual = next(mount for mount in django['volumes'] if mount['target'] == '/app/manuale')
            go_manual = next(mount for mount in golang['volumes'] if mount['target'] == '/app/manuale')
            self.assertEqual(go_manual['source'], django_manual['source'])
            self.assertFalse(go_manual.get('read_only', False), 'Go must refresh the same manual cache under its shared file lease')
            self.assertEqual(golang['environment']['MANUAL_INDEX_PATH'], '/app/manuale/index.json')
            self.assertEqual(golang['environment']['DATA_RESTORE_LOCK_PATH'],
                             django['environment']['DATA_RESTORE_LOCK_PATH'])
            for key in ('DBHOST', 'DBNAME', 'DBUSER', 'DBPASSWORD', 'REDIS_HOST',
                        'REDIS_PASSWORD', 'SECRET_KEY', 'JWT_SECRET_KEY_B64', 'JWT_PUBLIC_KEY_B64'):
                self.assertEqual(golang['environment'][key], django['environment'][key])
            self.assertTrue(any(mount['target'] == '/run/assozeta-operations' and
                                mount['source'] == directory + '/.operations'
                                for mount in golang['volumes']))
            self.assertIn('api-go', services['web']['depends_on'])
            self.assertIn('celery', services['worker']['command'])
            self.assertIn('celery', services['beat']['command'])
            self.assertNotIn('worker-go', services)
            self.assertNotIn('beat-go', services)

    def test_operational_migrator_reuses_go_image_configuration_without_http_health(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / '.env'
            env_file.write_text((ROOT / 'selfhost/.env.example').read_text())
            result = subprocess.run(['docker', 'compose', '--env-file', str(env_file), '-f',
                                     str(ROOT / 'selfhost/compose.yml'), '--profile', 'tools',
                                     'config', '--format', 'json'],
                                    env={**os.environ, 'ASSOZETA_ENV_FILE': str(env_file),
                                         'ASSOZETA_SELFHOST_DIR': directory},
                                    capture_output=True, text=True, check=True)
            services = json.loads(result.stdout)['services']
            self.assertIn('migrate-go', services)
            migration, api = services['migrate-go'], services['api-go']
            for field in ('image', 'user', 'read_only', 'security_opt'):
                self.assertEqual(migration[field], api[field], field)
            self.assertEqual(api['environment']['GO_LOGO_SPOOL_DIR'], '/var/cache/assozeta-logo')
            self.assertEqual(migration['environment'], {
                name: value for name, value in api['environment'].items()
                if name != 'GO_LOGO_SPOOL_DIR'})
            self.assertEqual(migration['volumes'], [
                volume for volume in api['volumes'] if volume['target'] != '/var/cache/assozeta-logo'])
            spool = [volume for volume in api['volumes'] if volume['target'] == '/var/cache/assozeta-logo']
            self.assertEqual(len(spool), 1)
            self.assertEqual(spool[0]['type'], 'volume')
            self.assertNotIn('source', spool[0])
            self.assertEqual(migration['entrypoint'], ['/app/migrate'])
            self.assertEqual(migration['profiles'], ['tools'])
            self.assertEqual(migration['restart'], 'no')
            self.assertTrue(migration['healthcheck']['disable'])
            self.assertEqual(set(migration['depends_on']), {'postgres'})


class GoLifecycleTests(unittest.TestCase):
    def run_shell(self, body, *, golang=True, migrator=True, config_failure=False, failure='', inherited_lease=False):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('APP_URL=http://fixture.invalid\n')
            harness = r'''
prod_compose() {
    if [ "$*" = 'config --services' ] || [ "$*" = '--profile tools config --services' ]; then
        [ "$TEST_CONFIG_FAILURE" = false ] || return 8
        printf 'api\nworker\nbeat\nweb\n'
        [ "$TEST_GO" = false ] || printf 'api-go\n'
        [ "$TEST_MIGRATOR" = false ] || printf 'migrate-go\n'
        return 0
    fi
    printf 'compose %s\n' "$*"
    case "$TEST_FAILURE:$*" in start:*up*api-go*) return 9 ;; esac
    case "$TEST_FAILURE:$*" in django:*run*' migrate') return 11 ;; go:*run*' migrate-go') return 12 ;; esac
}
require_background_services_running() { printf 'django-background\n'; }
wait_url_ready() { printf 'ready %s\n' "$2"; }
'''
            result = subprocess.run(['sh', '-c', DEFINITIONS + harness + body], capture_output=True, text=True,
                                    env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory,
                                         'TEST_GO': str(golang).lower(), 'TEST_CONFIG_FAILURE': str(config_failure).lower(),
                                         'TEST_MIGRATOR': str(migrator and golang).lower(),
                                         'prod_operations_lock_held': str(inherited_lease).lower(),
                                         'TEST_FAILURE': failure})
            return result, (root / '.operations/maintenance.flag').exists()

    def test_new_stack_stops_and_starts_both_apis_with_django_background_ownership(self):
        result, marker = self.run_shell('\nstop_prod_data_services\nstart_prod_app_services true\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn('compose stop api worker beat api-go', lines)
        self.assertIn('compose up -d --force-recreate worker beat', lines)
        self.assertIn('compose up -d --wait --force-recreate api web api-go', lines)
        self.assertIn('ready http://fixture.invalid/api/v1/readyz', lines)
        self.assertIn('ready http://fixture.invalid/api/v2/readyz', lines)
        self.assertFalse(marker)

    def test_historical_stack_never_passes_missing_go_service_to_compose(self):
        result, marker = self.run_shell('\nstop_prod_data_services\nstart_prod_app_services\n', golang=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('api-go', result.stdout)
        self.assertNotIn('/api/v1/', result.stdout)
        self.assertIn('ready http://fixture.invalid/api/readyz', result.stdout)
        self.assertFalse(marker)

    def test_failed_go_start_keeps_maintenance_and_never_claims_readiness(self):
        result, marker = self.run_shell('\nstop_prod_data_services\nstart_prod_app_services\n', failure='start')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker)
        self.assertNotIn('ready ', result.stdout)

    def test_failed_configuration_inspection_cannot_silently_skip_go(self):
        result, marker = self.run_shell('\nstop_prod_data_services\nstart_prod_app_services\n', config_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker)
        self.assertNotIn('compose up ', result.stdout)

    def test_initialisation_orders_both_migrations_before_any_application_resumes(self):
        result, marker = self.run_shell('\nprod_operations_lock_held=true\ninitialise_prod\nstart_prod_app_services\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        django = lines.index('compose --profile tools run --rm --no-deps migrate')
        golang = lines.index('compose --profile tools run --rm --no-deps migrate-go')
        self.assertLess(lines.index('compose stop api worker beat api-go'), django)
        self.assertLess(django, golang)
        self.assertLess(golang, lines.index('compose up -d worker beat'))
        self.assertFalse(marker)

    def test_migration_failure_retains_maintenance_and_stopped_applications(self):
        for failure, status in (('django', 11), ('go', 12)):
            with self.subTest(failure=failure):
                result, marker = self.run_shell('\nprod_operations_lock_held=true\ninitialise_prod\nstart_prod_app_services\n', failure=failure)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertTrue(marker)
                self.assertNotIn('compose up -d worker beat', result.stdout)
                self.assertNotIn('ready ', result.stdout)
                if failure == 'django':
                    self.assertNotIn('run --rm --no-deps migrate-go', result.stdout)

    def test_historical_source_without_operational_command_only_migrates_django(self):
        for golang in (False, True):
            with self.subTest(golang=golang):
                result, marker = self.run_shell('\ninitialise_prod\nstart_prod_app_services\n',
                                                golang=golang, migrator=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('run --rm --no-deps migrate\n', result.stdout)
                self.assertNotIn('migrate-go', result.stdout)
                self.assertFalse(marker)

    def test_go_migration_refuses_missing_caller_operations_lease(self):
        result, marker = self.run_shell('\ninitialise_prod\nstart_prod_app_services\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker)
        self.assertNotIn('run --rm --no-deps migrate', result.stdout)
        self.assertNotIn('compose stop', result.stdout)
        self.assertNotIn('compose up ', result.stdout)

    def test_migrate_cannot_accept_inherited_lease_assertion_or_stop_apps_without_lease(self):
        result, marker = self.run_shell(r'''
validate_prod() { :; }
prod_app_services_running() { return 0; }
migrate_prod
''', inherited_lease=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker)
        self.assertNotIn('compose ', result.stdout)

    def test_restore_refuses_missing_operations_lease_before_staging_or_replacement(self):
        result, marker = self.run_shell(r'''
preflight() { :; }
ensure_configuration_values() { :; }
: > "$SELFHOST_DIR/archive"
restore_prod "$SELFHOST_DIR/archive" --yes
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exclusive operations lease', result.stderr)
        self.assertNotIn('compose ', result.stdout)
        self.assertFalse(marker)

    def test_schema_helper_rejects_failed_service_discovery_before_django_migration(self):
        result, _ = self.run_shell('\nprod_operations_lock_held=true\nmigrate_prod_schemas\n', config_failure=True)
        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertNotIn('run ', result.stdout)

    def test_unmanaged_go_migrations_require_surviving_one_off_inspection(self):
        body = r'''
docker() {
    printf 'docker-inspection\n' >&2
    case "$TEST_FAILURE" in
        surviving)
            case "$*" in *com.docker.compose.oneoff=True*) printf 'migration-container\n' ;; esac ;;
        inspection)
            case "$*" in *com.docker.compose.oneoff=True*) return 7 ;; esac ;;
    esac
}
require_no_surviving_lifecycle_helpers
printf 'admitted\n'
'''
        for failure in ('', 'surviving', 'inspection'):
            with self.subTest(failure=failure):
                result, _ = self.run_shell(body, failure=failure)
                self.assertEqual(result.returncode == 0, not failure, result.stderr)
                self.assertIn('docker-inspection', result.stderr)
                self.assertEqual('admitted' in result.stdout, not failure)
        for golang in (False, True):
            with self.subTest(historical_go=golang):
                result, _ = self.run_shell(body, golang=golang, migrator=False, failure='inspection')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('docker-inspection', result.stderr)

    def test_first_configuration_has_no_migration_container_to_inspect(self):
        result, _ = self.run_shell(r'''
rm "$PROD_ENV"
docker() { printf 'unexpected Docker inspection\n' >&2; return 7; }
require_no_surviving_lifecycle_helpers
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('unexpected', result.stderr)

    def test_migration_caller_waits_for_real_operations_lease_and_retains_it_through_both_schemas(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.operations').mkdir()
            (root / '.env').write_text('APP_URL=http://fixture.invalid\n')
            lock = root / '.operations/lifecycle.lock'
            flock = root / 'flock'
            flock.write_text(f'#!{sys.executable}\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX)\n')
            flock.chmod(0o755)
            checker = root / 'check.py'
            checker.write_text('''import fcntl,os,sys
with open(os.environ['TEST_OPERATION_LOCK'], 'a') as handle:
    try:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        print('exclusive-lease:' + sys.argv[1], flush=True)
    else:
        raise SystemExit('migration caller did not retain exclusive operations lease')
''')
            script = DEFINITIONS + r'''
require_no_surviving_lifecycle_helpers() { :; }
validate_prod() { :; }
prod_app_services_running() { return 1; }
prod_compose() {
    case "$*" in
        'config --services'|'--profile tools config --services')
            printf 'api\napi-go\nworker\nbeat\nweb\nmigrate\nmigrate-go\n' ;;
        *'run --rm --no-deps migrate') "$TEST_PYTHON" "$TEST_CHECK" django ;;
        *'run --rm --no-deps migrate-go') "$TEST_PYTHON" "$TEST_CHECK" go ;;
        *) printf 'compose %s\n' "$*" ;;
    esac
}
run_locked_prod_command migrate
'''
            environment = {**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'],
                           'ASSOZETA_INSTALL_ROOT': str(root), 'TEST_PYTHON': sys.executable,
                           'TEST_CHECK': str(checker), 'TEST_OPERATION_LOCK': str(lock)}
            with lock.open('a') as held:
                fcntl.flock(held, fcntl.LOCK_SH)
                process = subprocess.Popen(['sh', '-c', script], env=environment,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        process.communicate(timeout=0.2)
                    self.assertFalse((root / '.operations/maintenance.flag').exists())
                    fcntl.flock(held, fcntl.LOCK_UN)
                    stdout, stderr = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertIn('exclusive-lease:django', stdout)
                    self.assertIn('exclusive-lease:go', stdout)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            with lock.open('a') as released:
                fcntl.flock(released, fcntl.LOCK_EX | fcntl.LOCK_NB)


class GoRunningVersionTests(unittest.TestCase):
    def exercise(self, fault=None, *, historical=False):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            images = {name: 'registry/' + name + '@sha256:' + 'a' * 64
                      for name in ('BACKEND', 'BACKEND_GO', 'WEB', 'RENDERER')}
            images['BACKEND_GO'] = 'ghcr.io/carbogninalberto/assozeta-backend-go@sha256:' + 'a' * 64
            env = root / '.env'
            env.write_text('ASSOZETA_VERSION=1.0.5\n' + ''.join(
                f'ASSOZETA_{name}_REF={image}\n' for name, image in images.items()
                if not ((historical or fault in ('dropped-ref', 'dropped-go')) and name == 'BACKEND_GO')))
            services = {'api': 'BACKEND', 'worker': 'BACKEND', 'beat': 'BACKEND', 'web': 'WEB', 'renderer': 'RENDERER'}
            if not historical and fault != 'dropped-go':
                services['api-go'] = 'BACKEND_GO'
            if not historical:
                (root / '.updater').mkdir()
                (root / '.updater/target-manifest.json').write_text(json.dumps({'version': '1.0.5',
                    'extensions': {'go_backend': {'schema_version': 1, 'image': images['BACKEND_GO']}}}))
            calls = []
            def docker(command, **kwargs):
                calls.append(command)
                if command[-3:] == ['config', '--format', 'json']:
                    return json.dumps({'services': {key: {'image': images[name]} for key, name in services.items()}})
                if command[:2] == ['docker', 'inspect']:
                    service = command[-1]
                    state = {'Running': not (service == 'api-go' and fault == 'stopped'), 'Health': {'Status':
                             'unhealthy' if service == 'api-go' and fault == 'health' else 'healthy'}}
                    if service == 'api-go' and fault == 'missing-health':
                        del state['Health']
                    return json.dumps([{'State': state,
                        'Config': {'Image': 'wrong' if service == 'api-go' and fault == 'image' else images[services[service]]}}])
                if command[-2:] == ['cat', '/app/VERSION']:
                    return '1.0.4' if 'api-go' in command and fault == 'version' else '1.0.5'
                if fault == 'missing' and command[-1] == 'api-go':
                    return ''
                return command[-1]
            with patch('engine.subprocess.check_output', side_effect=docker):
                Engine(root, env, None).verify_running('1.0.5', allow_configured_images=historical)
            return calls

    def test_new_go_image_health_and_embedded_version_are_required(self):
        calls = self.exercise()
        self.assertTrue(any(command[-3:] == ['api-go', 'cat', '/app/VERSION'] for command in calls))
        for fault in ('missing', 'health', 'missing-health', 'stopped', 'image', 'version', 'dropped-ref', 'dropped-go'):
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                self.exercise(fault)

    def test_historical_recovery_verifies_only_services_in_source_compose(self):
        calls = self.exercise(historical=True)
        self.assertFalse(any('api-go' in command for command in calls))


class GoRecoveryTests(unittest.TestCase):
    def test_go_cleanup_precedes_source_files_and_failure_leaves_distribution_intact(self):
        for golang, failed in ((True, False), (True, True), (False, False)):
            with self.subTest(golang=golang, failed=failed), TemporaryDirectory() as directory:
                root = Path(directory); (root / 'bin').mkdir()
                (root / 'bin/assozeta').write_text('target CLI')
                env = root / '.env'; env.write_text('COMPOSE_PROJECT_NAME=private-rehearsal\n')
                record = {'environment': str(env), 'recovery_image': 'verified-updater',
                          'backup': {'sha256': 'f' * 64}, 'phase': 'migration_started'}
                if golang:
                    record['target_go_backend_ref'] = 'verified-go'
                events = []
                def stop(*args):
                    events.append('stop-go')
                    if failed:
                        raise RuntimeError('stop failed')
                with patch('recovery.validate', return_value=record), \
                     patch('recovery.transaction', return_value=(root / 'transaction.json', record)), \
                     patch('recovery.stop_go_api_for_restore', side_effect=stop), \
                     patch('recovery.rollback', side_effect=lambda *a, **kw: events.append('restore-source')):
                    if failed:
                        with self.assertRaises(RuntimeError):
                            recovery.restore_files(root, root / 'archive')
                        self.assertEqual(events, ['stop-go'])
                    else:
                        recovery.restore_files(root, root / 'archive')
                        self.assertEqual(events, ['stop-go', 'restore-source'] if golang else ['restore-source'])

    def test_target_go_containers_are_stopped_and_removed_by_project_even_after_old_files_return(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); env = root / '.env'
            env.write_text('COMPOSE_PROJECT_NAME=private-rehearsal\n')
            calls = []
            def run(command, **kwargs):
                calls.append(command)
            with patch('recovery.subprocess.check_output', return_value='abc123\ndef456\n') as listing, \
                 patch('recovery.subprocess.run', side_effect=run):
                recovery.stop_go_api_for_restore(root, env)
            self.assertIn('label=com.docker.compose.project=private-rehearsal', listing.call_args.args[0])
            self.assertIn('label=com.docker.compose.service=api-go', listing.call_args.args[0])
            self.assertEqual(calls, [['docker', 'stop', '--time', '30', 'abc123', 'def456'],
                                     ['docker', 'rm', 'abc123', 'def456']])

    def test_failed_go_stop_prevents_removal_and_distribution_restore(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); env = root / '.env'; env.write_text('COMPOSE_PROJECT_NAME=private-rehearsal\n')
            with patch('recovery.subprocess.check_output', return_value='abc123\n'), \
                 patch('recovery.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['docker', 'stop'])) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    recovery.stop_go_api_for_restore(root, env)
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
