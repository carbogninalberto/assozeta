"""Phase 6 milestone B: full Go-mode independence of the installed lifecycle (CONTRACT P6-B v1).

Boundary: the distribution's own bin/assozeta on a simulated installation (p6_harness.py), the
updater's release verification (selfhost/updater/engine.py) on the same simulated containers,
and the shipped packaging files. Failures read 'P6B contract red: ...'.

Milestone B forbids, in each steady mode, every container of the other runtime: residents,
one-shot tools (including the schema migrators) and execs. Go mode migrates, documents and
operates with Go only; Django mode prepares the shared operational tables through Django and
never runs a Go command. Schema equivalence of the two migration paths is pinned by
test_p6_schema.py.

Contract pinned here:
  lifecycle matrix   install (Django, fresh), start, restart, migrate, backup, restore, sync-manual,
                     create-superuser, status, health and stop each run no opposite-runtime
                     container. In Go mode `migrate` runs exactly one schema one-shot, `migrate-go`
                     (/app/migrate), which also owns the business schema and the static API
                     documentation assets (no `collectstatic` in a Django container).
  switch             a runtime switch (Phase 5 controller) runs no schema migration at all: migrations
                     belong to lifecycle boundaries (install, upgrade, restore, explicit migrate).
  packaging          BE_GO/Dockerfile builds and ships /app/create-instance-superuser and /app/manuale;
                     the release workflow carries the Go image's manual from a Go image, not from the
                     previous Django image.
  reversal artifacts engine.Engine.verify_running(target) also verifies the image of the inactive
                     runtime that the target manifest pins (manifest images.backend in Go mode,
                     extensions.go_backend.image in Django mode, as ASSOZETA_BACKEND_REF /
                     ASSOZETA_BACKEND_GO_REF): `docker image inspect` must find it locally, otherwise
                     verification fails with a message naming the inactive runtime image. The release
                     manifest keeps schema_version 1 (no new fields are needed to pin both images), so
                     historical manifests stay accepted by bootstrap and recovery (green guard below).

Some tests need the Phase 5 controller (marked); they stay red until it lands.
Run: python3 -B -m unittest -v TESTS.handoff.test_p6_independence   (~6 min)
"""
import json
from pathlib import Path
import re
import sys
import unittest

from TESTS.handoff import p5_harness as h5
from TESTS.handoff import p6_harness as h
from TESTS.handoff.topology import ROOT

RED = 'P6B contract red'
VERIFY = """
import json, sys
import engine
try:
    engine.Engine(sys.argv[1], sys.argv[2], None).verify_running(sys.argv[3])
    print(json.dumps({'ok': True, 'error': None}))
except Exception as error:
    print(json.dumps({'ok': False, 'error': f'{type(error).__name__}: {error}'}))
"""
GO_REF = 'ghcr.io/carbogninalberto/assozeta-backend-go@sha256:' + 'b' * 64


def tearDownModule():
    h.stop_services()


class Base(h.ContractB, unittest.TestCase):
    maxDiff = None

    def assert_independent(self, inst, runtime, since, label):
        self.assertEqual(inst.opposite(runtime, since, milestone='B'), [],
                         f'{RED}: {label} in {runtime} mode ran an opposite-runtime container')


class LifecycleMatrix(Base):
    """Every lifecycle command in each steady mode, one-shot containers included."""

    def matrix(self, runtime):
        with h.installation(self, runtime) as inst:
            if runtime == 'django':
                with self.subTest(command='install'):
                    self.assert_independent(inst, runtime, 0, 'a fresh install')
            inst.set_env(MANUAL_CORPUS_BASE_URL='https://manual.example.invalid/corpus')
            inst.cli('backup')
            archive = inst.backups()[-1]
            for command in (('start',), ('restart',), ('migrate',), ('backup',), ('restore', str(archive), '--yes'),
                            ('sync-manual',), ('create-superuser', '--username', 'b6', '--email', 'b6@example.invalid',
                                               '--noinput'), ('status',), ('health',), ('stop',), ('start',)):
                with self.subTest(command=command[0]):
                    mark = inst.mark()
                    inst.cli(*command, check=False, env={'DJANGO_SUPERUSER_PASSWORD': 'fixture-password-b6'})
                    self.assert_independent(inst, runtime, mark, command[0])
                    if command == ('migrate',) and runtime == 'go':
                        migrators = [(item['service'], item['runtime']) for item in inst.executables(mark)
                                     if item['kind'] == 'oneshot' and item['service'] in ('migrate', 'migrate-go')]
                        self.assertEqual(migrators, [('migrate-go', 'go')],
                                         f'{RED}: Go mode must migrate with the Go migrator only')

    def test_django_lifecycle_runs_no_go_container(self):
        self.matrix('django')

    def test_go_lifecycle_runs_no_django_container(self):
        self.matrix('go')


class FailedUpgradeIndependence(Base):
    """Upgrade and its recovery, target image failing, without a single opposite-runtime container."""

    def independent(self, runtime):
        with h.installation(self, install=False) as inst:
            outcome = h.failed_upgrade(self, inst, runtime)
            if outcome['recover'] is None:
                raise h5.HarnessError(f'the failed upgrade left no recoverable transaction: '
                                      f'{outcome["upgrade"].stdout[-600:]}')
            self.assert_independent(inst, runtime, outcome['marks'][0], 'the failed upgrade and its recovery')

    def test_go_upgrade_and_recovery_run_no_django_container(self):
        self.independent('go')

    def test_django_upgrade_and_recovery_run_no_go_container(self):
        self.independent('django')


class SwitchWithoutMigration(Base):
    """Needs the Phase 5 controller: a runtime switch is not a lifecycle boundary."""

    def switch(self, source, target):
        with h.installation(self, source) as inst:
            mark = inst.mark()
            try:
                inst.switch(target)
            except AssertionError as error:
                self.fail(f'{RED}: the runtime switch {source} -> {target} was not admitted (Phase 5 controller): '
                          f'{str(error)[-300:]}')
            final = inst.runtime_records()[-1]
            self.assertEqual(final.get('status'), 'succeeded',
                             f'{RED}: the runtime switch {source} -> {target} did not succeed (Phase 5 controller): '
                             f'{final.get("error_code")} {final.get("error")}')
            migrations = [item for item in inst.executables(mark)
                          if item['kind'] == 'oneshot' and item['service'] in ('migrate', 'migrate-go')]
            self.assertEqual(migrations, [], f'{RED}: a runtime switch ran a schema migration')
            self.assert_independent(inst, target, inst.mark(), 'the switched installation')

    def test_switch_to_go_runs_no_schema_migration(self):
        self.switch('django', 'go')

    def test_switch_to_django_runs_no_schema_migration(self):
        self.switch('go', 'django')


class Packaging(Base):
    def test_go_image_ships_the_native_superuser_and_manual_commands(self):
        dockerfile = (ROOT / 'BE_GO/Dockerfile').read_text()
        for command, package in (('create-instance-superuser', 'create-instance-superuser'), ('manuale', 'manuale')):
            with self.subTest(command=command):
                self.assertRegex(dockerfile, rf'go build[^\n]*-o /{command} \./cmd/{package}\b',
                                 f'{RED}: BE_GO/Dockerfile does not build ./cmd/{package}')
                self.assertRegex(dockerfile, rf'COPY --from=builder /{command} /app/{command}\b',
                                 f'{RED}: the Go image does not ship /app/{command}')

    def test_release_carries_the_go_manual_from_a_go_image(self):
        workflow = (ROOT / '.github/workflows/publish-images.yml').read_text()
        match = re.search(r'- name: Carry the manual of the previous release\n(.*?)(?=\n      - name: )', workflow, re.S)
        self.assertIsNotNone(match, 'the publish workflow has no manual carry step (contract changed)')
        step = match.group(1)
        sources = re.findall(r'SOURCE_IMAGE:\s*(\S.*)', step)
        self.assertTrue(sources, 'the carry step names no SOURCE_IMAGE')
        fixed_django = [value for value in sources if re.search(r'assozeta-backend:', value)]
        self.assertEqual(fixed_django, [], f'{RED}: the Go image carries its manual from the previous Django image '
                                           f'({fixed_django}); carry it from a Go image')


class ReversalArtifacts(Base):
    """Upgrade verification pins and verifies both runtime images, so a later reversal works offline."""

    def verify(self, inst, absent=()):
        inst.set_state(images_absent=list(absent))
        result = inst.run_python(VERIFY, str(inst.root), str(inst.env_file), '2.0.1',
                                 extra_path=(h5.UPDATER, ROOT / 'BE/instance'))
        lines = result.stdout.strip().splitlines()
        if not lines or not lines[-1].startswith('{'):
            raise h5.HarnessError(f'verification driver failed: {result.stderr[-800:]}')
        return json.loads(lines[-1])

    def reversal(self, runtime):
        with h.installation(self, install=False) as inst:
            h.managed_installation(inst, runtime)
            manifest = {'schema_version': 1, 'version': '2.0.1', 'bundle_sha256': 'a' * 64,
                        'images': {name: h.release_ref(name, 1) for name in h.RELEASE_IMAGES},
                        'extensions': {'go_backend': {'schema_version': 1, 'image': h.release_ref('backend-go', 1)}},
                        'files': {'compose.yml': 'a' * 64}}
            (inst.root / '.updater/target-manifest.json').write_text(json.dumps(manifest))
            control = self.verify(inst)
            if not control['ok']:
                raise h5.HarnessError(f'verification of a healthy {runtime} installation failed: {control["error"]}')
            inactive = h.release_ref('backend' if runtime == 'go' else 'backend-go', 1)
            mark = inst.mark()
            outcome = self.verify(inst, absent=[inactive])
            self.assertFalse(outcome['ok'], f'{RED}: {runtime}-mode verification accepted a release whose inactive '
                                            'runtime image is missing (no reversal possible)')
            inspected = [e for e in inst.events()[mark:] if e.get('call', [])[:2] == ['image', 'inspect']
                         and inactive in e['call']]
            self.assertTrue(inspected, f'{RED}: verification never inspected the inactive runtime image')

    def test_go_mode_verification_requires_the_django_image(self):
        self.reversal('go')

    def test_django_mode_verification_requires_the_go_image(self):
        self.reversal('django')

    def test_historical_manifests_stay_accepted(self):
        """Green guard: the manifest contract is not versioned away from recovery."""
        for path in (str(ROOT / 'BE/instance'), str(ROOT / 'selfhost/updater')):
            if path not in sys.path:
                sys.path.insert(0, path)
        import distribution
        core = {'schema_version': 1, 'version': '1.0.17', 'bundle_sha256': 'a' * 64,
                'images': {name: f'ghcr.io/carbogninalberto/assozeta-{name}@sha256:' + 'd' * 64
                           for name in ('backend', 'web', 'renderer', 'updater')},
                'files': {'compose.yml': 'e' * 64}}
        distribution.validate_manifest(dict(core), 'v1.0.17')
        distribution.validate_manifest({**core, 'version': '1.0.48', 'extensions': {
            'go_backend': {'schema_version': 1, 'image': GO_REF}}}, 'v1.0.48', require_go_backend=True)


if __name__ == '__main__':
    unittest.main()
