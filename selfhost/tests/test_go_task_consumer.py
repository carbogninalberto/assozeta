"""Opt-in Go Celery consumer (worker-go): Compose profile, lifecycle and routing guard."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from common import compose_command
from engine import Engine
import recovery
from restart import service_order

CLI = ROOT / 'selfhost/bin/assozeta'
DEFINITIONS = CLI.read_text().split('\ncommand=${1:-help}\n')[0]
ROUTED = 'core.tasks.send_mail_async'


def compose_services(env_file, directory, *profiles):
    arguments = ['docker', 'compose', '--env-file', str(env_file), '-f', str(ROOT / 'selfhost/compose.yml')]
    for profile in profiles:
        arguments += ['--profile', profile]
    result = subprocess.run(arguments + ['config', '--format', 'json'], capture_output=True, text=True, check=True,
                            env={**os.environ, 'ASSOZETA_ENV_FILE': str(env_file), 'ASSOZETA_SELFHOST_DIR': directory})
    services = json.loads(result.stdout)['services']
    assert services, 'compose rendered no services'
    return services


class WorkerGoComposeTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.env = Path(self.directory) / '.env'
        self.env.write_text((ROOT / 'selfhost/.env.example').read_text())

    def test_default_stack_has_no_go_consumer(self):
        services = compose_services(self.env, self.directory)
        self.assertNotIn('worker-go', services)
        self.assertIn('worker', services)
        services = compose_services(self.env, self.directory, 'tools')
        self.assertNotIn('worker-go', services)

    def test_profile_renders_hardened_consumer_with_api_go_configuration(self):
        services = compose_services(self.env, self.directory, 'go-tasks')
        consumer, api = services['worker-go'], services['api-go']
        for field in ('image', 'user', 'read_only', 'security_opt', 'init', 'tmpfs', 'env_file', 'networks', 'restart'):
            self.assertEqual(consumer.get(field), api.get(field), field)
        self.assertEqual(consumer['user'], '10001:10001')
        self.assertTrue(consumer['read_only'])
        self.assertIn('no-new-privileges:true', consumer['security_opt'])
        self.assertEqual(list(consumer['networks']), ['internal'])
        self.assertEqual(consumer['profiles'], ['go-tasks'])
        self.assertEqual(consumer['entrypoint'], ['/app/celery-consumer'])
        self.assertEqual(consumer['command'], ['--concurrency=2', '--drain-timeout=50s'])
        self.assertEqual(consumer['stop_grace_period'], '1m0s')
        self.assertTrue(consumer['healthcheck']['disable'])
        self.assertEqual(set(consumer['depends_on']), {'postgres', 'redis', 'minio'})
        self.assertTrue(all(dependency['condition'] == 'service_healthy' for dependency in consumer['depends_on'].values()))
        self.assertEqual(consumer['environment']['DATA_RESTORE_LOCK_PATH'], api['environment']['DATA_RESTORE_LOCK_PATH'])
        self.assertEqual(consumer['environment']['ASSOZETA_DEPLOYMENT_MODE'], 'production')
        self.assertEqual(consumer['environment']['TMPDIR'], '/var/cache/assozeta-go-tasks')
        for key in ('DBHOST', 'DBNAME', 'DBUSER', 'DBPASSWORD', 'REDIS_HOST', 'REDIS_PASSWORD', 'SECRET_KEY',
                    'AWS_S3_ENDPOINT_URL', 'AWS_STORAGE_BUCKET_NAME', 'EMAIL_HOST', 'CURRENT_HOST'):
            self.assertEqual(consumer['environment'][key], api['environment'][key], key)
        self.assertNotIn('GO_LOGO_SPOOL_DIR', consumer['environment'])
        volumes = {volume['target']: volume for volume in consumer['volumes']}
        self.assertEqual(set(volumes), {'/run/assozeta-operations', '/var/cache/assozeta-go-tasks'})
        self.assertEqual(volumes['/run/assozeta-operations']['source'], self.directory + '/.operations')
        self.assertEqual(volumes['/var/cache/assozeta-go-tasks']['type'], 'volume')
        self.assertNotIn('source', volumes['/var/cache/assozeta-go-tasks'])
        self.assertFalse(any('docker.sock' in volume['target'] or 'updater' in volume.get('source', '')
                             for volume in consumer['volumes']))
        self.assertNotIn('ports', consumer)

    def test_concurrency_follows_its_own_setting_then_celery_concurrency(self):
        self.env.write_text(self.env.read_text().replace('CELERY_CONCURRENCY=2', 'CELERY_CONCURRENCY=3'))
        self.assertIn('--concurrency=3', compose_services(self.env, self.directory, 'go-tasks')['worker-go']['command'])
        with self.env.open('a') as handle:
            handle.write('GO_CELERY_CONCURRENCY=5\n')
        self.assertIn('--concurrency=5', compose_services(self.env, self.directory, 'go-tasks')['worker-go']['command'])

    def test_django_worker_consumes_only_the_default_queue(self):
        command = compose_services(self.env, self.directory, 'go-tasks')['worker']['command']
        self.assertEqual(command[command.index('-Q') + 1], 'celery')

    def test_image_builds_and_ships_the_consumer_with_its_staging_directory(self):
        dockerfile = (ROOT / 'BE_GO/Dockerfile').read_text()
        self.assertIn('go build -trimpath -o /celery-consumer ./cmd/celery-consumer', dockerfile)
        self.assertIn('COPY --from=builder /celery-consumer /app/celery-consumer', dockerfile)
        self.assertIn('chown 10001:10001 /var/cache/assozeta-go-tasks && chmod 0700 /var/cache/assozeta-go-tasks',
                      dockerfile)


class WorkerGoCliComposeTests(unittest.TestCase):
    """prod_compose passes the profile from ASSOZETA_GO_TASKS to real Compose."""

    def services(self, flag, *arguments):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(ROOT / 'selfhost/compose.yml', root / 'compose.yml')
            env = root / '.env'
            env.write_text((ROOT / 'selfhost/.env.example').read_text().replace(
                'ASSOZETA_GO_TASKS=0', f'ASSOZETA_GO_TASKS={flag}'))
            self.assertIn(f'ASSOZETA_GO_TASKS={flag}\n', env.read_text())
            result = subprocess.run(['sh', '-c', DEFINITIONS + '\nprod_compose "$@"\n', 'test', *arguments],
                                    capture_output=True, text=True,
                                    env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory})
            self.assertEqual(result.returncode, 0, result.stderr)
            services = result.stdout.split()
            self.assertIn('api', services)
            return services

    def test_flag_selects_the_profile_alongside_tools(self):
        self.assertNotIn('worker-go', self.services(0, 'config', '--services'))
        self.assertIn('worker-go', self.services(1, 'config', '--services'))
        with_tools = self.services(1, '--profile', 'tools', 'config', '--services')
        self.assertIn('worker-go', with_tools)
        self.assertIn('migrate-go', with_tools)


HARNESS = r'''
compose_with_env() {
    shift 2
    case " $* " in
        *' config --services '*)
            [ "$TEST_CONFIG_FAILURE" = false ] || return 8
            printf 'api\nworker\nbeat\nweb\nrealtime-relay\napi-go\n'
            case " $* " in *' --profile tools '*) printf 'migrate\nmigrate-go\n' ;; esac
            if [ "$TEST_DEFINED" = true ]; then
                case " $* " in *' --profile go-tasks '*) printf 'worker-go\n' ;; esac
            fi
            return 0 ;;
        *' ps --status running '*)
            for service in "$@"; do
                case "$service" in ps|--status|running|--services|--profile|go-tasks) continue ;; esac
                [ "$service" = "$TEST_NOT_RUNNING" ] || printf '%s\n' "$service"
            done
            return 0 ;;
    esac
    printf 'compose %s\n' "$*"
}
wait_url_ready() { printf 'ready %s\n' "$2"; }
'''


class WorkerGoLifecycleTests(unittest.TestCase):
    def run_shell(self, body, *, flag='', names='', defined=True, not_running='', config_failure=False):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('APP_URL=http://fixture.invalid\n'
                                       f'ASSOZETA_GO_TASKS={flag}\nGO_TASK_QUEUE_TASKS={names}\n')
            result = subprocess.run(['sh', '-c', DEFINITIONS + HARNESS + body], capture_output=True, text=True,
                                    env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory,
                                         'TEST_DEFINED': str(defined).lower(), 'TEST_NOT_RUNNING': not_running,
                                         'TEST_CONFIG_FAILURE': str(config_failure).lower()})
            return result, result.stdout.splitlines(), (root / '.operations/maintenance.flag').exists()

    def test_enabled_consumer_stops_with_the_django_worker_and_starts_after_migrations(self):
        result, lines, marker = self.run_shell('\nprod_operations_lock_held=true\ninitialise_prod\n'
                                               'start_prod_app_services true\n', flag='1', names=ROUTED)
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = lines.index('compose --profile go-tasks stop worker-go api worker beat realtime-relay api-go')
        migrate = lines.index('compose --profile go-tasks --profile tools run --rm --no-deps migrate')
        start = lines.index('compose --profile go-tasks up -d --force-recreate worker beat realtime-relay worker-go')
        self.assertLess(stop, migrate)
        self.assertLess(migrate, start)
        self.assertLess(start, lines.index('compose --profile go-tasks up -d --wait --force-recreate api web api-go'))
        self.assertIn('ready http://fixture.invalid/api/v2/readyz', lines)
        self.assertFalse(marker)

    def test_disabled_consumer_is_still_stopped_but_never_started(self):
        for flag in ('', '0'):
            with self.subTest(flag=flag):
                result, lines, marker = self.run_shell('\nstop_prod_data_services\nstart_prod_app_services\n', flag=flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('compose --profile go-tasks stop worker-go api worker beat realtime-relay api-go', lines)
                self.assertIn('compose up -d worker beat realtime-relay', lines)
                self.assertEqual(sum('worker-go' in line for line in lines), 1)
                self.assertFalse(marker)

    def test_historical_distribution_never_names_the_consumer(self):
        result, lines, marker = self.run_shell('\nstop_prod_data_services\nstart_prod_app_services\n', defined=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('compose stop api worker beat realtime-relay api-go', lines)
        self.assertNotIn('worker-go', result.stdout)
        self.assertFalse(marker)

    def test_backup_stop_and_restart_drain_the_consumer(self):
        result, lines, _ = self.run_shell('\nstop_prod_data_services\nbackup_app_stopped=true\n'
                                          'backup_app_was_running=true\nbackup_cleanup 0\n', flag='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(lines.index('compose --profile go-tasks stop worker-go api worker beat realtime-relay api-go'),
                        lines.index('compose --profile go-tasks up -d worker beat realtime-relay worker-go'))

    def test_consumer_that_does_not_stay_running_fails_start_and_keeps_maintenance(self):
        result, lines, marker = self.run_shell('\nstop_prod_data_services\nstart_prod_app_services\n',
                                               flag='1', not_running='worker-go')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('worker-go did not remain running', result.stdout + result.stderr)
        self.assertTrue(marker)
        self.assertFalse(any(line.startswith('ready ') for line in lines))

    def test_running_detection_includes_the_enabled_consumer(self):
        result, _, _ = self.run_shell('\nprod_app_services_running && printf "running\\n"\n', flag='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('running', result.stdout)

    def test_failed_discovery_cannot_skip_the_consumer(self):
        result, lines, marker = self.run_shell('\nstop_prod_data_services\n', flag='1', config_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(' stop ' in line for line in lines))


class GoTaskRoutingGuardTests(unittest.TestCase):
    def guard(self, flag, names, defined=True):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('APP_URL=http://fixture.invalid\n'
                                       f'ASSOZETA_GO_TASKS={flag}\nGO_TASK_QUEUE_TASKS={names}\n')
            return subprocess.run(['sh', '-c', DEFINITIONS + HARNESS + '\nstart_prod_app_services\n'],
                                  capture_output=True, text=True,
                                  env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory,
                                       'TEST_DEFINED': str(defined).lower(), 'TEST_NOT_RUNNING': '',
                                       'TEST_CONFIG_FAILURE': 'false'})

    def test_routed_tasks_without_the_consumer_refuse_to_start_anything(self):
        for flag in ('', '0'):
            for names in (ROUTED, f' {ROUTED} , ', f'"{ROUTED}"'):
                with self.subTest(flag=flag, names=names):
                    result = self.guard(flag, names)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('ASSOZETA_GO_TASKS=1 is not set', result.stderr)
                    self.assertNotIn('compose ', result.stdout)

    def test_empty_routing_list_needs_no_consumer(self):
        for names in ('', ' , ', '""'):
            with self.subTest(names=names):
                result = self.guard('0', names)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('worker-go', result.stdout)

    def test_enabled_consumer_accepts_routes(self):
        for names in ('', ROUTED):
            with self.subTest(names=names):
                result = self.guard('1', names)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('worker-go', result.stdout)

    def test_invalid_flag_or_missing_service_is_refused(self):
        result = self.guard('yes', '')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ASSOZETA_GO_TASKS must be 0 or 1', result.stderr)
        result = self.guard('1', ROUTED, defined=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no worker-go service', result.stderr)
        self.assertNotIn('compose ', result.stdout)

    def test_cli_validate_applies_the_guard_to_a_generated_configuration(self):
        with TemporaryDirectory() as directory:
            env = Path(directory) / 'prod.env'
            environment = {**os.environ, 'ASSOZETA_ENV_FILE': str(env)}
            subprocess.run([str(CLI), 'configure', '--domain', 'localhost', '--version', 'test'],
                           env=environment, capture_output=True, text=True, check=True)
            generated = env.read_text()
            self.assertIn('ASSOZETA_GO_TASKS=0\n', generated)
            self.assertIn('GO_TASK_QUEUE_TASKS=\n', generated)
            for flag, accepted in (('0', False), ('1', True)):
                with self.subTest(flag=flag):
                    env.write_text(generated.replace('ASSOZETA_GO_TASKS=0', f'ASSOZETA_GO_TASKS={flag}').replace(
                        'GO_TASK_QUEUE_TASKS=\n', f'GO_TASK_QUEUE_TASKS={ROUTED}\n'))
                    result = subprocess.run([str(CLI), 'validate'], env=environment, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, accepted, result.stderr)
                    if not accepted:
                        self.assertIn('ASSOZETA_GO_TASKS=1 is not set', result.stderr)


class WorkerGoUpdaterTests(unittest.TestCase):
    def test_compose_command_adds_profile_only_for_enabled_application_project(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); env = root / '.env'
            for flag, expected in (('', False), ('0', False), ('1', True)):
                with self.subTest(flag=flag):
                    env.write_text(f'ASSOZETA_GO_TASKS={flag}\n')
                    self.assertEqual('go-tasks' in compose_command(root, env), expected)
                    self.assertNotIn('go-tasks', compose_command(root, env, updater=True))

    def test_restart_includes_the_consumer_but_never_tools(self):
        services = {'postgres': {}, 'redis': {}, 'api': {'depends_on': {'postgres': {}}},
                    'worker-go': {'profiles': ['go-tasks'], 'depends_on': {'postgres': {}, 'redis': {}}},
                    'migrate-go': {'profiles': ['tools'], 'depends_on': {'postgres': {}}}}
        order = service_order(services)
        self.assertIn('worker-go', order)
        self.assertNotIn('migrate-go', order)
        self.assertLess(order.index('postgres'), order.index('worker-go'))

    def verify(self, fault=None):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            images = {'BACKEND': 'registry/backend@sha256:' + 'a' * 64, 'BACKEND_GO': 'registry/go@sha256:' + 'b' * 64,
                      'WEB': 'registry/web@sha256:' + 'c' * 64, 'RENDERER': 'registry/renderer@sha256:' + 'd' * 64}
            env = root / '.env'
            env.write_text('ASSOZETA_VERSION=1.0.5\nASSOZETA_GO_TASKS=1\n' +
                           ''.join(f'ASSOZETA_{name}_REF={image}\n' for name, image in images.items()))
            services = {'api': 'BACKEND', 'worker': 'BACKEND', 'beat': 'BACKEND', 'web': 'WEB', 'renderer': 'RENDERER',
                        'api-go': 'BACKEND_GO', 'worker-go': 'BACKEND_GO'}
            calls = []

            def docker(command, **kwargs):
                calls.append(command)
                if command[-3:] == ['config', '--format', 'json']:
                    self.assertIn('go-tasks', command)
                    return json.dumps({'services': {key: {'image': images[name]} for key, name in services.items()}})
                if command[:2] == ['docker', 'inspect']:
                    service = command[-1]
                    running = not (service == 'worker-go' and fault == 'stopped')
                    image = 'wrong' if service == 'worker-go' and fault == 'image' else images[services[service]]
                    return json.dumps([{'State': {'Running': running, 'Health': {'Status': 'healthy'}},
                                        'Config': {'Image': image}}])
                if command[-2:] == ['cat', '/app/VERSION']:
                    return '1.0.5'
                if fault == 'missing' and command[-1] == 'worker-go':
                    return ''
                return command[-1]

            with patch('engine.subprocess.check_output', side_effect=docker):
                Engine(root, env, None).verify_running('1.0.5')
            return calls

    def test_running_version_check_covers_the_enabled_consumer(self):
        calls = self.verify()
        self.assertIn(['docker', 'inspect', 'worker-go'], calls)
        for fault in ('missing', 'stopped', 'image'):
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                self.verify(fault)

    def test_recovery_stops_and_removes_api_go_and_worker_go(self):
        with TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            env.write_text('COMPOSE_PROJECT_NAME=private-rehearsal\n')
            listings = {'api-go': 'abc123\n', 'worker-go': 'fed987\n'}
            calls = []

            def listing(command, **kwargs):
                self.assertIn('label=com.docker.compose.project=private-rehearsal', command)
                service = command[-1].removeprefix('label=com.docker.compose.service=')
                return listings[service]

            with patch('recovery.subprocess.check_output', side_effect=listing), \
                 patch('recovery.subprocess.run', side_effect=lambda command, **kwargs: calls.append(command)):
                recovery.stop_go_api_for_restore(directory, env)
            self.assertEqual(calls, [['docker', 'stop', '--time', '30', 'abc123', 'fed987'],
                                     ['docker', 'rm', 'abc123', 'fed987']])


if __name__ == '__main__':
    unittest.main()
