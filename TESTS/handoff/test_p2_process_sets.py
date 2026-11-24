"""Phase 2 (process sets): production Compose describes one application process set per runtime.

Boundary: the distribution's own `selfhost/compose.yml`, rendered by the real
Docker Compose CLI (`docker compose config`, no daemon) after the
distribution's own `bin/assozeta configure`. Expected red until Phase 2
(TESTS/handoff/expected_red.json, owner t-p2).

Contract (bus topic p2, t-p2):
  - Runtime profiles. Every long-running application service has exactly one
    Compose profile, `django` or `go`, and no other:
      django: api, worker, beat, realtime-relay
      go:     api-go, worker-go, scheduler-go, job-worker-go, realtime-relay-go
    Shared services (postgres, redis, minio, renderer, web) have no profile;
    one-shot tools (minio-init, migrate, migrate-go) keep `tools`. Rendering
    with `--profile <mode>` therefore yields exactly that mode's application
    services plus shared ones, and a render without a runtime profile starts
    no application process at all. The lifecycle passes `--profile <mode>`
    for the effective runtime it read from the Phase 1 authority.
  - Exactly one relay: the active mode's. Legacy preferences
    (ASSOZETA_GO_TASKS, GO_TASK_QUEUE_TASKS, ASSOZETA_REALTIME_RELAY_BACKEND and
    its replica counts, ASSOZETA_BAKNEY_PROTOCOL_BACKEND, the old `go-tasks`
    profile) cannot add, remove or swap a service of either set.
  - `web` depends on no application service (the dependency that forced both
    APIs up is gone).
  - New Go services `scheduler-go` (/app/scheduler) and `job-worker-go`
    (/app/worker): Go backend image, profile `go`, uid 10001, read-only root,
    /tmp tmpfs, no-new-privileges, init, restart unless-stopped, json-file
    logging, the operations mount and DATA_RESTORE_LOCK_PATH, a pids limit and
    a memory limit, stop grace longer than the worker's --drain-timeout, and
    health-gated dependencies (scheduler: postgres; job worker: postgres,
    redis, minio). The scheduler joins only the internal network.
  - api-go never hands requests to Django: DJANGO_HANDOFF_URL renders empty
    whatever .env or the caller's environment says.
  - api-go receives ASSOZETA_GO_WHATSAPP_OUTBOX from the caller (default 1),
    so a private preflight can start it with the WhatsApp poller off.
  - Stable internal alias `active-api`, declared by api and api-go on both
    `internal` and `renderer_egress`, by no other service. Generated
    CURRENT_HOST / CURRENT_HOST_PUPPETEER name hosts that resolve, in each
    mode, to exactly the active API on the network their callers use; the
    renderer allowlist contains the PUPPETEER host and minio and no
    runtime-specific API host (api, api-go); ALLOWED_HOSTS contains both hosts.

    python3 -B -m TESTS.handoff.run_expected_red TESTS.handoff.test_p2_process_sets
"""
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import urlsplit

from TESTS.handoff.p2_harness import (API_ALIAS, APP, ACTIVE_API, DJANGO_APP, EMAIL_TASKS, GO_APP, GO_JOB_WORKER,
                                      GO_SCHEDULER, RELAY, RUNTIME_PROFILES, SHARED, WHATSAPP_GATE, aliases,
                                      application, configured_installation, env_values, long_running,
                                      networks_of, render, resolution)

BAD = 'Go background service definition'
LEGACY_GO_PREFERENCES = {
    'ASSOZETA_GO_TASKS': '1', 'GO_TASK_QUEUE_TASKS': EMAIL_TASKS,
    'ASSOZETA_REALTIME_RELAY_BACKEND': 'go', 'ASSOZETA_DJANGO_RELAY_REPLICAS': '0', 'ASSOZETA_GO_RELAY_REPLICAS': '1',
    'ASSOZETA_BAKNEY_PROTOCOL_BACKEND': 'go', 'ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM': 'api-go:8000',
}
LEGACY_DJANGO_PREFERENCES = {
    'ASSOZETA_GO_TASKS': '0', 'GO_TASK_QUEUE_TASKS': '',
    'ASSOZETA_REALTIME_RELAY_BACKEND': 'django', 'ASSOZETA_DJANGO_RELAY_REPLICAS': '1', 'ASSOZETA_GO_RELAY_REPLICAS': '0',
    'ASSOZETA_BAKNEY_PROTOCOL_BACKEND': 'django', 'ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM': 'api:8000',
}


def seconds(value):
    """Compose duration ('1m0s', '35s', '1m30s') in seconds."""
    total, text = 0.0, str(value or '')
    for amount, unit in re.findall(r'(\d+(?:\.\d+)?)(h|ms|m|s)', text):
        total += float(amount) * {'h': 3600, 'm': 60, 's': 1, 'ms': 0.001}[unit]
    return total


class ConfiguredCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='assozeta-p2-compose-')
        self.addCleanup(directory.cleanup)
        self.installation = configured_installation(Path(directory.name))

    def services(self, *profiles, environment=None):
        return render(self.installation, *profiles, environment=environment)['services']

    def describe(self, services):
        return {name: (definition.get('profiles'), (definition.get('deploy') or {}).get('replicas'))
                for name, definition in sorted(services.items())}


class RuntimeProcessSets(ConfiguredCase):
    def test_each_runtime_profile_renders_exactly_its_application_services(self):
        for mode in RUNTIME_PROFILES:
            with self.subTest(mode=mode):
                services = self.services(mode)
                self.assertTrue(SHARED <= long_running(services), self.describe(services))
                self.assertEqual(sorted(application(services)), sorted(APP[mode]),
                                 f'long-running application services for runtime {mode} (compose --profile {mode}) '
                                 f'differ from the {mode} process set\n{self.describe(services)}')

    def test_every_application_service_has_exactly_one_runtime_profile(self):
        everything = self.services('*')
        found = {name: definition.get('profiles') for name, definition in everything.items()
                 if name in DJANGO_APP | GO_APP or (name not in SHARED and 'tools' not in (definition.get('profiles') or []))}
        expected = {**{name: ['django'] for name in DJANGO_APP}, **{name: ['go'] for name in GO_APP}}
        self.assertEqual(found, expected, 'runtime profile per application service')
        without_runtime = application(self.services())
        self.assertEqual(sorted(without_runtime), [],
                         'application services start without a runtime profile: ' + ', '.join(sorted(without_runtime)))

    def test_web_depends_on_no_application_service(self):
        for profiles in (('django',), ('go',), ('*',)):
            with self.subTest(profiles=profiles):
                web = self.services(*profiles)['web']
                dependencies = set(web.get('depends_on') or {})
                self.assertEqual(sorted(dependencies & (DJANGO_APP | GO_APP)), [],
                                 'web depends on application services: '
                                 + ', '.join(sorted(dependencies & (DJANGO_APP | GO_APP))))

    def test_legacy_go_preferences_cannot_change_the_django_process_set(self):
        self.installation.set_env(**LEGACY_GO_PREFERENCES)
        # An older lifecycle passed --profile go-tasks whenever ASSOZETA_GO_TASKS=1.
        for profiles in (('django',), ('django', 'go-tasks')):
            with self.subTest(profiles=profiles):
                services = self.services(*profiles)
                self.assertEqual(sorted(application(services)), sorted(DJANGO_APP),
                                 f'long-running application services for runtime django with legacy Go preferences '
                                 f'(compose --profile {" --profile ".join(profiles)})\n{self.describe(services)}')

    def test_legacy_django_preferences_cannot_change_the_go_process_set(self):
        self.installation.set_env(**LEGACY_DJANGO_PREFERENCES)
        services = self.services('go')
        self.assertEqual(sorted(application(services)), sorted(GO_APP),
                         'long-running application services for runtime go with legacy Django preferences\n'
                         + str(self.describe(services)))

    def test_exactly_one_relay_per_mode_whatever_the_relay_setting(self):
        relays = {'realtime-relay', 'realtime-relay-go'}
        for backend, (django_replicas, go_replicas) in (('django', ('1', '0')), ('go', ('0', '1'))):
            self.installation.set_env(ASSOZETA_REALTIME_RELAY_BACKEND=backend,
                                      ASSOZETA_DJANGO_RELAY_REPLICAS=django_replicas,
                                      ASSOZETA_GO_RELAY_REPLICAS=go_replicas)
            for mode in RUNTIME_PROFILES:
                with self.subTest(setting=backend, mode=mode):
                    running = application(self.services(mode)) & relays
                    self.assertEqual(sorted(running), [RELAY[mode]],
                                     f'relays in runtime {mode} with ASSOZETA_REALTIME_RELAY_BACKEND={backend}')


class GoBackgroundServices(ConfiguredCase):
    """The native Go scheduler and durable job worker as production services."""

    def definitions(self):
        services = self.services('go')
        missing = [name for name in (GO_SCHEDULER, GO_JOB_WORKER) if name not in services]
        self.assertEqual(missing, [], f'{BAD}: missing from production Compose (profile go): '
                         + ', '.join(missing))
        return services

    def test_scheduler_and_job_worker_run_the_go_binaries_with_the_go_image(self):
        services = self.definitions()
        for name, binary in ((GO_SCHEDULER, '/app/scheduler'), (GO_JOB_WORKER, '/app/worker')):
            with self.subTest(service=name):
                definition = services[name]
                self.assertEqual(definition.get('entrypoint'), [binary], f'{BAD} {name} entrypoint')
                self.assertEqual(definition.get('image'), services['api-go']['image'], f'{BAD} {name} image')
                self.assertEqual(definition.get('profiles'), ['go'], f'{BAD} {name} profiles')
                self.assertEqual(definition.get('restart'), 'unless-stopped', f'{BAD} {name} restart policy')
                self.assertNotIn('ports', definition, f'{BAD} {name} publishes ports')

    def test_scheduler_and_job_worker_permissions_storage_and_network(self):
        services = self.definitions()
        for name in (GO_SCHEDULER, GO_JOB_WORKER):
            with self.subTest(service=name):
                definition = services[name]
                self.assertEqual(definition.get('user'), '10001:10001', f'{BAD} {name} user')
                self.assertIs(definition.get('read_only'), True, f'{BAD} {name} read-only root')
                self.assertIs(definition.get('init'), True, f'{BAD} {name} init')
                self.assertIn('no-new-privileges:true', definition.get('security_opt') or [], f'{BAD} {name} security options')
                self.assertTrue(any(item.split(':', 1)[0] == '/tmp' for item in definition.get('tmpfs') or []),
                                f'{BAD} {name} /tmp tmpfs: {definition.get("tmpfs")}')
                self.assertEqual((definition.get('logging') or {}).get('driver'), 'json-file', f'{BAD} {name} logging')
                self.assertEqual((definition.get('environment') or {}).get('DATA_RESTORE_LOCK_PATH'),
                                 '/run/assozeta-operations/lifecycle.lock', f'{BAD} {name} restore lock path')
                self.assertIn('/run/assozeta-operations', [volume.get('target') for volume in definition.get('volumes') or []],
                              f'{BAD} {name} operations mount')
                self.assertIn('internal', networks_of(definition), f'{BAD} {name} networks')
                self.assertNotIn(API_ALIAS, aliases(definition, 'internal') | aliases(definition, 'renderer_egress'),
                                 f'{BAD} {name} holds the API alias')
        self.assertEqual(networks_of(services[GO_SCHEDULER]), {'internal'}, f'{BAD} {GO_SCHEDULER} networks')

    def test_scheduler_and_job_worker_resource_limits_and_stop_grace(self):
        services = self.definitions()
        for name in (GO_SCHEDULER, GO_JOB_WORKER):
            with self.subTest(service=name):
                definition = services[name]
                limits = ((definition.get('deploy') or {}).get('resources') or {}).get('limits') or {}
                pids = definition.get('pids_limit') or limits.get('pids') or 0
                self.assertGreater(int(pids), 0, f'{BAD} {name} has no pids limit')
                memory = limits.get('memory') or definition.get('mem_limit')
                self.assertTrue(memory, f'{BAD} {name} has no memory limit')
                self.assertGreaterEqual(seconds(definition.get('stop_grace_period')), 10, f'{BAD} {name} stop grace')
        worker = services[GO_JOB_WORKER]
        drain = [item for item in worker.get('command') or [] if item.startswith('--drain-timeout=')]
        self.assertEqual(len(drain), 1, f'{BAD} {GO_JOB_WORKER} must set an explicit --drain-timeout: {worker.get("command")}')
        self.assertGreater(seconds(worker.get('stop_grace_period')), seconds(drain[0].split('=', 1)[1]),
                           f'{BAD} job worker stop grace must exceed its drain timeout')

    def test_scheduler_and_job_worker_wait_for_healthy_dependencies(self):
        services = self.definitions()
        for name, required in ((GO_SCHEDULER, {'postgres'}), (GO_JOB_WORKER, {'postgres', 'redis', 'minio'})):
            with self.subTest(service=name):
                dependencies = services[name].get('depends_on') or {}
                self.assertTrue(required <= set(dependencies), f'{BAD} {name} dependencies {dependencies}')
                for dependency in required:
                    self.assertEqual(dependencies[dependency].get('condition'), 'service_healthy', f'{BAD} {name} waits for {dependency}')
                self.assertEqual(sorted(set(dependencies) & (DJANGO_APP | GO_APP)), [],
                                 f'{BAD} {name} must not pull up an application service')


class GoApiStartupEffects(ConfiguredCase):
    def test_api_go_never_hands_requests_to_django(self):
        cases = (('default', None, None),
                 ('.env sets DJANGO_HANDOFF_URL', {'DJANGO_HANDOFF_URL': 'http://api:8000'}, None),
                 ('caller exports DJANGO_HANDOFF_URL', None, {'DJANGO_HANDOFF_URL': 'http://api:8000'}))
        for label, env, process in cases:
            with self.subTest(case=label):
                if env:
                    self.installation.set_env(**env)
                value = (self.services('go', environment=process)['api-go'].get('environment') or {}).get(
                    'DJANGO_HANDOFF_URL', '')
                self.assertEqual(value, '', f'api-go hands refused requests to Django ({label}): '
                                            f'DJANGO_HANDOFF_URL={value}')

    def test_whatsapp_outbox_gate_is_passed_to_api_go(self):
        enabled = (self.services('go')['api-go'].get('environment') or {}).get(WHATSAPP_GATE)
        disabled = (self.services('go', environment={WHATSAPP_GATE: '0'})['api-go'].get('environment') or {}).get(
            WHATSAPP_GATE)
        self.assertEqual((enabled, disabled), ('1', '0'),
                         f'api-go {WHATSAPP_GATE} (default, caller sets 0)')


class InternalApiReferences(ConfiguredCase):
    def test_only_the_two_apis_declare_the_active_api_alias_on_both_networks(self):
        everything = self.services('*')
        holders = {name: sorted(network for network in ('internal', 'renderer_egress')
                                if API_ALIAS in aliases(definition, network))
                   for name, definition in everything.items()}
        holders = {name: networks for name, networks in holders.items() if networks}
        self.assertEqual(holders, {'api': ['internal', 'renderer_egress'], 'api-go': ['internal', 'renderer_egress']},
                         f'services declaring the {API_ALIAS} alias')

    def test_generated_internal_api_references_resolve_to_the_active_api_in_each_mode(self):
        values = env_values(self.installation.env_file)
        current, puppeteer = urlsplit(values['CURRENT_HOST']).hostname, urlsplit(values['CURRENT_HOST_PUPPETEER']).hostname
        for mode in RUNTIME_PROFILES:
            with self.subTest(mode=mode):
                services = self.services(mode)
                running = long_running(services)
                self.assertEqual(resolution(services, running, current, 'internal'), {ACTIVE_API[mode]},
                                 f'CURRENT_HOST={values["CURRENT_HOST"]} does not reach exactly the active API '
                                 f'({ACTIVE_API[mode]}) in runtime {mode}')
                self.assertEqual(resolution(services, running, puppeteer, 'renderer_egress'), {ACTIVE_API[mode]},
                                 f'CURRENT_HOST_PUPPETEER={values["CURRENT_HOST_PUPPETEER"]} does not reach exactly '
                                 f'the active API ({ACTIVE_API[mode]}) from the renderer in runtime {mode}')
        allowed = set(values['ALLOWED_HOSTS'].split(','))
        self.assertTrue({current, puppeteer} <= allowed,
                        f'generated ALLOWED_HOSTS={values["ALLOWED_HOSTS"]} does not reach exactly the active API host')

    def test_renderer_allowlist_follows_the_puppeteer_reference(self):
        values = env_values(self.installation.env_file)
        puppeteer = urlsplit(values['CURRENT_HOST_PUPPETEER']).hostname
        allowed = {host.strip() for host in
                   self.services('*')['renderer']['environment']['RENDER_ALLOWED_HOSTS'].split(',')}
        self.assertIn(puppeteer, allowed, 'the renderer allowlist does not allow CURRENT_HOST_PUPPETEER')
        self.assertIn('minio', allowed, 'the renderer allowlist does not allow minio')
        self.assertEqual(sorted(allowed & {'api', 'api-go'}), [],
                         'the renderer allowlist names a runtime-specific API host: '
                         + ', '.join(sorted(allowed & {'api', 'api-go'})))


if __name__ == '__main__':
    unittest.main()
