"""Phase 0 target-topology acceptance tests and the legacy dual-topology baseline.

Two boundaries, both driven by the distribution's own `bin/assozeta`:
  1. lifecycle: Compose rendered by the real Docker Compose CLI, containers
     simulated (TESTS/handoff/fake_docker.py); this yields the exact resident
     services and one-shot containers a command leaves behind;
  2. proxy: the installation's own Caddyfile in the pinned Caddy image, where
     only running application services resolve (needs a Docker daemon).

`TargetTopology` holds the two expected-red contracts of the plan. They are
tracked in TESTS/handoff/expected_red.json and run through
`python3 -B -m TESTS.handoff.run_expected_red`; they are never release evidence.
`LegacyDualTopology` characterizes historical distributions (the plan's
baseline revision) and must stay green.

Resident services only: the first milestone still allows maintenance-only
one-shot migration tools (migrate, migrate-go). They are reported, not gated.
"""
import json
from pathlib import Path
import tempfile
import unittest

from TESTS.handoff.topology import (BASELINE_REVISION, COLD_CLIENT_FIRST_REQUEST, DJANGO_RESIDENT,
                                    Installation, caddy_routes)

SHARED = {'postgres', 'redis', 'minio', 'renderer', 'web'}
EMAIL_TASKS = 'core.tasks.send_mail_async,send_email_text,send_email_template,send_email_html'
# The operator entry point for an instance-wide runtime change, as Phase 1 pins
# it (bus #4, t-p1): synchronous, exit 0 only for a committed or no-op outcome.
# `runtime-status` prints JSON with `effective_runtime`. This test only calls them.
REQUEST_GO = ('runtime-switch', 'go')
RUNTIME_STATUS = ('runtime-status',)


class InstallationCase(unittest.TestCase):
    revision = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='assozeta-handoff-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.installation = Installation(self.directory, revision=self.revision)

    def proxy(self):
        log = (self.directory / 'proxy.log').open('w')
        self.addCleanup(log.close)
        return caddy_routes(self.installation, log)

    def runtimes(self):
        running = self.installation.running()
        return running, {name for name, runtime in running.items() if runtime == 'go'}, \
            {name for name, runtime in running.items() if runtime == 'django'}


class TargetTopology(InstallationCase):
    """Expected red on this revision; turned green by Phases 2 and 5."""

    def test_fresh_install_runs_no_go_application_process(self):
        self.installation.install()
        running, golang, django = self.runtimes()
        # Positive control: a dead simulation must not pass by running nothing.
        self.assertTrue(SHARED <= set(running), self.installation.describe())
        self.assertTrue(set(DJANGO_RESIDENT) <= django, self.installation.describe())
        self.assertEqual(sorted(golang), [],
                         'Go application services running on a fresh installation: ' + ', '.join(sorted(golang))
                         + '\n' + self.installation.describe())

    def test_go_request_leaves_only_go_application_processes_and_serves_cold_client(self):
        self.installation.install()
        before, _, django_before = self.runtimes()
        self.assertTrue(set(DJANGO_RESIDENT) <= django_before, self.installation.describe())
        result = self.installation.cli(*REQUEST_GO, check=False)
        self.assertEqual(result.returncode, 0,
                         f'Go request rejected: bin/assozeta {" ".join(REQUEST_GO)} exited {result.returncode}: '
                         + (result.stderr.strip() or result.stdout.strip())[-600:])
        running, golang, django = self.runtimes()
        self.assertEqual(sorted(django), [],
                         'Django application services still running after the Go request: '
                         + ', '.join(sorted(django)) + '\n' + self.installation.describe())
        self.assertTrue(SHARED <= set(running), self.installation.describe())
        self.assertTrue(golang, 'no Go application service runs after the Go request\n' + self.installation.describe())
        with self.proxy() as request:
            status, marker = request(COLD_CLIENT_FIRST_REQUEST)
        self.assertEqual((status, marker.get('X-Fixture-Runtime')), (200, 'go'),
                         f'cold client GET {COLD_CLIENT_FIRST_REQUEST} served by {marker or "nothing"} '
                         f'(HTTP {status}) after the Go request')
        output = self.installation.cli(*RUNTIME_STATUS).stdout
        reported = json.loads(output[output.index('{'):output.rindex('}') + 1])
        self.assertEqual(reported.get('effective_runtime'), 'go', reported)


class LegacyDualTopology(InstallationCase):
    """Historical distribution (baseline revision): both APIs up, Caddy chooses by path."""
    revision = BASELINE_REVISION

    def test_fresh_install_runs_both_apis_with_django_background(self):
        self.installation.install()
        running = self.installation.running()
        self.assertEqual(running, {
            'postgres': 'shared', 'redis': 'shared', 'minio': 'shared', 'renderer': 'shared', 'web': 'shared',
            'api': 'django', 'worker': 'django', 'beat': 'django', 'realtime-relay': 'django',
            'api-go': 'go'}, self.installation.describe())
        oneoffs = [entry['service'] for entry in self.installation.oneoffs()]
        self.assertEqual(oneoffs, ['minio-init', 'migrate', 'migrate-go'])

    def test_proxy_chooses_backend_by_path(self):
        self.installation.install()
        with self.proxy() as request:
            observed = {path: request(path) for path in (
                COLD_CLIENT_FIRST_REQUEST, '/api/v1/instance/status', '/api/v2/instance/status',
                '/ws/notifications/', '/api/v2/ws/notifications/', '/bakney/v1/pairing')}
        self.assertEqual({path: (status, marker.get('X-Fixture-Backend')) for path, (status, marker) in observed.items()}, {
            COLD_CLIENT_FIRST_REQUEST: (200, 'api'),
            '/api/v1/instance/status': (200, 'api'),
            '/api/v2/instance/status': (200, 'api-go'),
            '/ws/notifications/': (200, 'api'),
            '/api/v2/ws/notifications/': (200, 'api-go'),
            '/bakney/v1/pairing': (200, 'api'),
        })

    def test_every_existing_go_control_keeps_django_resident_and_the_default_alias(self):
        """The legacy mixed topology: per-component Go settings never stop Django or move /api."""
        self.installation.install()
        self.installation.set_env(ASSOZETA_GO_TASKS='1', GO_TASK_QUEUE_TASKS=EMAIL_TASKS,
                                  ASSOZETA_REALTIME_RELAY_BACKEND='go', ASSOZETA_BAKNEY_PROTOCOL_BACKEND='go')
        self.installation.cli('restart')
        running = self.installation.running()
        self.assertEqual(running, {
            'postgres': 'shared', 'redis': 'shared', 'minio': 'shared', 'renderer': 'shared', 'web': 'shared',
            'api': 'django', 'worker': 'django', 'beat': 'django',
            'api-go': 'go', 'worker-go': 'go', 'realtime-relay-go': 'go'}, self.installation.describe())
        with self.proxy() as request:
            cold = request(COLD_CLIENT_FIRST_REQUEST)
            bakney = request('/bakney/v1/pairing')
        self.assertEqual((cold[0], cold[1].get('X-Fixture-Backend')), (200, 'api'))
        self.assertEqual((bakney[0], bakney[1].get('X-Fixture-Backend')), (200, 'api-go'))


if __name__ == '__main__':
    unittest.main()
