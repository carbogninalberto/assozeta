"""Phase 2 (lifecycle): the CLI starts, stops and checks the process set of the effective runtime.

Boundary: the distribution's own `bin/assozeta` against the real Docker Compose
CLI with simulated containers (TESTS/handoff/p2_docker.py, which keeps
fake_docker.py's model and adds the Phase 1 authority row, container
existence and real Compose profile scoping). Expected red until Phase 2
(TESTS/handoff/expected_red.json, owner t-p2).

Contract (bus topic p2, t-p2):
  - The CLI reads the effective runtime from the Phase 1 authority only:
    `SELECT runtime ... FROM public.assozeta_go_job_ownership WHERE singleton=1`
    through `prod_compose exec -T postgres psql -X -qAt -v ON_ERROR_STOP=1`
    (the same read as runtime_switch.compose_authority). No row means django
    (fresh or unset). An unreadable authority starts no application service
    and the command fails. No new .env setting selects the runtime.
  - Shell functions (one source for every lifecycle path): `prod_effective_runtime`
    prints django|go; `runtime_app_services MODE` prints that mode's long-running
    application services in this distribution, derived from Compose
    (`--profile MODE`); runtime services are always addressed with an explicit
    `--profile` (real Compose ignores profile services in a bare stop/down).
  - install/start/restart/migrate/backup/restore/upgrade start exactly the
    shared services plus `runtime_app_services <effective>`; stopping stops
    the application services of both runtimes; the readiness/health check
    requires every service of the active set to stay running (a crashed
    scheduler-go fails the start and keeps maintenance on).
  - The inactive set stays stopped after a later restart in the other mode
    and has no `restart: always` policy (a daemon or host restart cannot
    revive it).
  - Legacy Go preferences in Django mode start no Go service. Task names routed
    to the Go queue (GO_TASK_QUEUE_TASKS) in Django mode are refused before any
    application service starts, naming GO_TASK_QUEUE_TASKS.
  - Already-installed CURRENT_HOST / CURRENT_HOST_PUPPETEER equal to the old
    generated value (http://api:8000) are rewritten to the runtime-independent
    reference (host active-api) and ALLOWED_HOSTS gains that host, keeping its
    entries. Other values are operator overrides: preserved byte for byte;
    `bin/assozeta validate` (and so every lifecycle command) fails naming the
    key when an override names a runtime-specific API host (api, api-go) or
    when CURRENT_HOST_PUPPETEER names a host the renderer does not allow.

    python3 -B -m TESTS.handoff.run_expected_red TESTS.handoff.test_p2_lifecycle
"""
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

from TESTS.handoff.p2_harness import (API_ALIAS, APP, ACTIVE_API, DJANGO_APP, EMAIL_TASKS, GO_APP, GO_SCHEDULER,
                                      SHARED, ModeInstallation, env_values, long_running, render, resolution)


class LifecycleCase(unittest.TestCase):
    revision = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='assozeta-p2-lifecycle-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.installation = ModeInstallation(self.directory, revision=self.revision)

    def installed(self, runtime='django'):
        self.installation.set_authority('django')
        self.installation.install()
        if runtime != 'django':
            self.installation.set_authority(runtime)
        return self.installation

    def assertProcessSet(self, mode, context):
        running = self.installation.running()
        application = self.installation.application_running()
        self.assertTrue(SHARED <= set(running), f'shared services missing after {context}\n'
                        + self.installation.describe())
        self.assertEqual(sorted(application), sorted(APP[mode]),
                         f'application services running after {context} in runtime {mode}\n'
                         + self.installation.describe())


class EffectiveRuntimeProcessSet(LifecycleCase):
    def test_fresh_install_reads_the_authority_and_runs_only_django(self):
        self.installed('django')
        self.assertProcessSet('django', 'a fresh install')
        self.assertGreaterEqual(self.installation.authority_reads(), 1,
                                'the lifecycle never read the runtime authority')

    def test_install_without_an_authority_row_runs_django(self):
        self.installation.authority_without_row()
        self.installation.install()
        self.assertProcessSet('django', 'a fresh install without an authority row')

    def test_start_and_restart_in_go_mode_run_only_the_go_process_set(self):
        for command in ('start', 'restart'):
            with self.subTest(command=command):
                self.setUp()
                self.installed('go')
                self.installation.cli(command)
                self.assertProcessSet('go', f'bin/assozeta {command}')

    def test_migrate_restarts_the_running_go_process_set(self):
        self.installed('go')
        self.installation.cli('restart', check=False)
        self.installation.cli('migrate')
        self.assertProcessSet('go', 'bin/assozeta migrate')

    def test_stop_stops_both_process_sets(self):
        for runtime in ('django', 'go'):
            with self.subTest(runtime=runtime):
                self.setUp()
                self.installed(runtime)
                self.installation.cli('restart', check=False)
                self.installation.cli('stop')
                left = self.installation.application_running()
                self.assertEqual(sorted(left), [], f'application services still running after bin/assozeta stop '
                                                   f'in runtime {runtime}: ' + ', '.join(sorted(left)))

    def test_unreadable_authority_starts_no_application_service(self):
        self.installed('django')
        self.installation.authority_unavailable()
        result = self.installation.cli('restart', check=False)
        left = self.installation.application_running()
        self.assertEqual((result.returncode != 0, sorted(left)), (True, []),
                         f'restart with an unreadable runtime authority exited {result.returncode} and left '
                         f'application services running: {", ".join(sorted(left))}\n' + self.installation.describe())

    def test_crashed_go_scheduler_fails_the_start_and_keeps_maintenance(self):
        self.installed('go')
        self.installation.crash(GO_SCHEDULER)
        result = self.installation.cli('restart', check=False)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0,
                            f'restart in runtime go succeeded although {GO_SCHEDULER} did not stay running\n'
                            + self.installation.describe())
        self.assertIn(GO_SCHEDULER, output, f'the failed start does not name {GO_SCHEDULER}')
        self.assertTrue((self.installation.root / '.operations/maintenance.flag').exists(),
                        'maintenance was lifted after a failed health check')


class InactiveSetStaysStopped(LifecycleCase):
    def test_round_trip_restarts_leave_the_inactive_set_stopped(self):
        self.installed('go')
        self.installation.cli('restart')
        self.assertProcessSet('go', 'restart into runtime go')
        self.installation.set_authority('django', generation=3)
        self.installation.cli('restart')
        self.assertProcessSet('django', 'restart back into runtime django')
        self.installation.set_authority('go', generation=4)
        self.installation.cli('restart')
        self.assertProcessSet('go', 'a second restart into runtime go')

    def test_no_application_service_restarts_on_its_own_after_a_daemon_restart(self):
        everything = render(self.installed('django'), '*')
        always = sorted(name for name, definition in everything['services'].items()
                        if name in DJANGO_APP | GO_APP and definition.get('restart') == 'always')
        self.assertEqual(always, [], 'application services a daemon restart would revive: ' + ', '.join(always))
        # The positive side: the inactive set is stopped, not merely unhealthy.
        self.installation.cli('restart')
        self.assertEqual(sorted(self.installation.application_running() & GO_APP), [],
                         'Go application services running in runtime django')


class LegacyPreferences(LifecycleCase):
    def test_legacy_go_preferences_in_django_mode_start_no_go_service(self):
        self.installed('django')
        self.installation.set_env(ASSOZETA_GO_TASKS='1', GO_TASK_QUEUE_TASKS='',
                                  ASSOZETA_REALTIME_RELAY_BACKEND='go', ASSOZETA_BAKNEY_PROTOCOL_BACKEND='go')
        self.installation.cli('restart')
        self.assertProcessSet('django', 'restart with legacy Go preferences')

    def test_routed_go_tasks_in_django_mode_are_refused_before_starting(self):
        self.installed('django')
        self.installation.set_env(ASSOZETA_GO_TASKS='1', GO_TASK_QUEUE_TASKS=EMAIL_TASKS)
        result = self.installation.cli('restart', check=False)
        left = self.installation.application_running()
        self.assertEqual((result.returncode != 0, 'GO_TASK_QUEUE_TASKS' in result.stdout + result.stderr,
                          sorted(left & GO_APP)), (True, True, []),
                         f'GO_TASK_QUEUE_TASKS routed to Go in runtime django: restart exited {result.returncode}, '
                         f'Go services running: {", ".join(sorted(left & GO_APP))}\n'
                         + (result.stderr or result.stdout)[-600:])

    def test_go_mode_ignores_the_legacy_task_switch(self):
        self.installed('go')
        self.installation.set_env(ASSOZETA_GO_TASKS='0', GO_TASK_QUEUE_TASKS='',
                                  ASSOZETA_REALTIME_RELAY_BACKEND='django', ASSOZETA_BAKNEY_PROTOCOL_BACKEND='django')
        self.installation.cli('restart')
        self.assertProcessSet('go', 'restart with legacy Django preferences')


class InternalReferenceConfiguration(LifecycleCase):
    OLD = 'http://api:8000'

    def test_installed_default_references_move_to_the_runtime_independent_api(self):
        self.installed('django')
        old_allowed = 'localhost,api,localhost,127.0.0.1'
        self.installation.set_env(CURRENT_HOST=self.OLD, CURRENT_HOST_PUPPETEER=self.OLD, ALLOWED_HOSTS=old_allowed)
        self.installation.cli('restart')
        values = env_values(self.installation.env_file)
        for key in ('CURRENT_HOST', 'CURRENT_HOST_PUPPETEER'):
            host = urlsplit(values[key]).hostname
            for mode in ('django', 'go'):
                services = render(self.installation, mode)['services']
                network = 'renderer_egress' if key == 'CURRENT_HOST_PUPPETEER' else 'internal'
                with self.subTest(key=key, mode=mode):
                    self.assertEqual(resolution(services, long_running(services), host, network), {ACTIVE_API[mode]},
                                     f'already-installed {key}={values[key]} does not reach exactly the active API '
                                     f'({ACTIVE_API[mode]}) in runtime {mode}')
        allowed = values['ALLOWED_HOSTS'].split(',')
        self.assertIn(API_ALIAS, allowed, f'already-installed ALLOWED_HOSTS lacks {API_ALIAS}: {values["ALLOWED_HOSTS"]}')
        self.assertEqual([item for item in allowed if item != API_ALIAS], old_allowed.split(','),
                         'already-installed ALLOWED_HOSTS lost or reordered entries')

    def validate(self):
        return self.installation.cli('validate', check=False)

    def test_runtime_specific_override_is_preserved_and_reported(self):
        self.installed('django')
        for key, value in (('CURRENT_HOST', 'http://api-go:8000'), ('CURRENT_HOST_PUPPETEER', 'http://api-go:8000'),
                           ('CURRENT_HOST', 'http://api:8001'),
                           ('CURRENT_HOST_PUPPETEER', 'http://render-source.example.invalid:8000')):
            with self.subTest(key=key, value=value):
                before = env_values(self.installation.env_file)
                self.installation.set_env(**{key: value})
                result = self.validate()
                self.assertEqual((result.returncode != 0, key in result.stdout + result.stderr), (True, True),
                                 f'validate accepted the incompatible override {key}={value} '
                                 f'(exit {result.returncode})')
                self.assertEqual(env_values(self.installation.env_file)[key], value, f'override {key} was rewritten')
                self.installation.set_env(**{key: before[key]})

    def test_compatible_override_is_preserved(self):
        self.installed('django')
        self.installation.set_env(CURRENT_HOST='https://override.example.invalid')
        self.assertEqual(self.validate().returncode, 0, 'validate rejected a compatible CURRENT_HOST override')
        self.installation.cli('restart')
        self.assertEqual(env_values(self.installation.env_file)['CURRENT_HOST'], 'https://override.example.invalid')


if __name__ == '__main__':
    unittest.main()
