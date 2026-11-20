"""Phase 2 (updater): restart, live verification and service discovery reuse the per-mode process set.

Boundary: the updater modules shipped in the distribution (`updater/` inside
the installation, i.e. selfhost/updater) run as the runner would, against the
real Docker Compose CLI and simulated containers (TESTS/handoff/p2_docker.py,
which also answers the Phase 1 authority read). Expected red until Phase 2
(TESTS/handoff/expected_red.json, owner t-p2).

Contract (bus topic p2, t-p2):
  - `common.runtime_services(root, env_file, runtime) -> list[str]`: the
    sorted long-running application services of `runtime` that the
    installation's own Compose file defines (profile `runtime`). For a
    historical distribution without runtime profiles it returns the legacy
    set that distribution runs (both APIs, Django worker, Beat and relay), so
    the target updater can still verify and recover an old source.
  - The effective runtime comes from `runtime_switch.read_authority(root,
    env_file)` (Phase 1); nothing new is stored in .env.
  - `restart.Restart(...).inventory()` lists only shared services and the
    effective runtime's services, even when stopped containers of the other
    runtime exist (a controller restart cannot revive a rolled-back runtime).
  - `engine.Engine(root, env_file, None).verify_running(version,
    allow_configured_images=True)` requires exactly the effective set: it
    accepts an installation running only that set and rejects one where an
    application service of the other runtime runs.

    python3 -B -m TESTS.handoff.run_expected_red TESTS.handoff.test_p2_updater
"""
import json
from pathlib import Path
import tempfile
import unittest

from TESTS.handoff.p2_harness import APP, DJANGO_APP, GO_APP, SHARED, ModeInstallation
from TESTS.handoff.topology import BASELINE_REVISION, ROOT

LEGACY_DJANGO_SET = ['api', 'api-go', 'beat', 'realtime-relay', 'worker']
VERSION = '1.4.0'
SNIPPET = r'''
import json, sys
root, env, action = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    if action.startswith('runtime_services:'):
        import common
        if not hasattr(common, 'runtime_services'):
            print(json.dumps({'error': 'CONTRACT MISSING: updater common.runtime_services'})); raise SystemExit
        print(json.dumps({'result': common.runtime_services(root, env, action.split(':', 1)[1])}))
    elif action == 'inventory':
        from journal import Journal
        from restart import Restart
        from pathlib import Path
        restart = Restart(root, env, Journal(Path(root) / '.updater/operations.sqlite3'),
                          '6b1f0c4e-2f61-4b8e-9d55-0d1c2a3b4c5d')
        items = restart.inventory()
        print(json.dumps({'result': sorted(item['Config']['Labels']['com.docker.compose.service'] for item in items)}))
    elif action.startswith('verify:'):
        from engine import Engine
        try:
            Engine(root, env, None).verify_running(action.split(':', 1)[1], allow_configured_images=True)
            print(json.dumps({'result': 'accepted'}))
        except Exception as error:
            print(json.dumps({'result': 'rejected', 'reason': f'{type(error).__name__}: {error}'}))
except Exception as error:
    print(json.dumps({'error': f'{type(error).__name__}: {error}'}))
'''


class UpdaterCase(unittest.TestCase):
    revision = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='assozeta-p2-updater-')
        self.addCleanup(directory.cleanup)
        self.installation = ModeInstallation(Path(directory.name), revision=self.revision)

    def updater(self, action, code_root=None):
        """Run an updater action with the installation's own updater (or `code_root`)."""
        code = Path(code_root) if code_root else self.installation.root / 'updater'
        result = self.installation.run_python(SNIPPET, str(self.installation.root), str(self.installation.env_file),
                                              action, extra_path=(code, ROOT / 'BE/instance'))
        lines = [line for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertTrue(lines, f'updater action {action} produced no result: {result.stderr[-1500:]}')
        return json.loads(lines[-1])

    def installed(self, runtime):
        self.installation.set_authority('django')
        self.installation.install()
        self.installation.set_env(ASSOZETA_VERSION=VERSION)
        if runtime != 'django':
            self.installation.set_authority(runtime)
            self.installation.cli('restart', check=False)
        return self.installation


class RuntimeServiceDiscovery(UpdaterCase):
    def test_runtime_services_per_mode(self):
        self.installation.cli('configure', '--domain', 'localhost', '--version', 'test')
        for mode in ('django', 'go'):
            with self.subTest(mode=mode):
                answer = self.updater(f'runtime_services:{mode}')
                self.assertEqual(answer.get('result'), sorted(APP[mode]),
                                 f'updater runtime_services for runtime {mode}: {answer}')


class HistoricalRuntimeServiceDiscovery(UpdaterCase):
    revision = BASELINE_REVISION

    def test_runtime_services_of_a_historical_distribution_are_its_legacy_set(self):
        self.installation.cli('configure', '--domain', 'localhost', '--version', 'test')
        # The current (target) updater operates on the historical source distribution.
        target = Path(self.installation.root).parent / 'target'
        target.mkdir()
        current = ModeInstallation(target)
        answer = self.updater('runtime_services:django', code_root=current.root / 'updater')
        self.assertEqual(answer.get('result'), LEGACY_DJANGO_SET,
                         f'updater runtime_services for a historical distribution: {answer}')


class ControllerRestart(UpdaterCase):
    def test_restart_helper_restarts_only_the_active_set(self):
        for runtime in ('go', 'django'):
            with self.subTest(runtime=runtime):
                self.setUp()
                self.installed('go')
                if runtime == 'django':
                    # Rolled back: Go containers exist, stopped.
                    self.installation.set_authority('django', generation=3)
                    self.installation.cli('restart', check=False)
                answer = self.updater('inventory')
                self.assertEqual(answer.get('result'), sorted(SHARED | APP[runtime]),
                                 f'restart helper inventory in runtime {runtime}: {answer}\n'
                                 + self.installation.describe())


class LiveVerification(UpdaterCase):
    def test_live_verification_requires_exactly_the_active_set(self):
        for runtime in ('django', 'go'):
            with self.subTest(runtime=runtime):
                self.setUp()
                self.installed(runtime)
                accepted = self.updater(f'verify:{VERSION}')
                self.assertEqual(accepted.get('result'), 'accepted',
                                 f'live verification in runtime {runtime} rejected the active set: {accepted}\n'
                                 + self.installation.describe())
                other = sorted(GO_APP if runtime == 'django' else DJANGO_APP)[0]
                state = self.installation.observed()
                state['running'] = sorted(set(state['running']) | {other})
                state['definitions'].setdefault(other, {})
                self.installation.state.write_text(json.dumps(state))
                rejected = self.updater(f'verify:{VERSION}')
                self.assertEqual(rejected.get('result'), 'rejected',
                                 f'live verification in runtime {runtime} accepted a running {other}: {rejected}')


if __name__ == '__main__':
    unittest.main()
