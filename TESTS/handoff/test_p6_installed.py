"""Phase 6 installed Go-mode lifecycle lane (CONTRACT P6-A/P6-B v1, installed boundary).

A real installation of this commit, exactly as Phase 5 part D builds it (TESTS/handoff/test_p5_installed.py
`Installed`: the five images built from the checkout, the distribution CLI, the updater runner, real
Compose, PostgreSQL, Redis, MinIO, renderer and Caddy, an owner configured through first-run setup).
The installation is brought to Go mode the way p6_harness does it (no work in flight): the Phase 1
SQL function assozeta_commit_runtime_authority moves the authority to Go, then the installation's own
`bin/assozeta restart` starts the Go set. Phase 6 does not depend on the Phase 5 controller here.

Lanes, in order (one installation; each lane leaves it in Go mode):
  1 lifecycle      after the restart only the shared services and the Go set run; the Go API
                   serves readiness and a settings write (milestone A).
  2 health         `bin/assozeta health` passes every check on the Go signals (milestone A).
  3 diagnostics    the owner diagnostics (POST /api/instance/admin/diagnostics, served by Go) report the
                   worker and the scheduler as passed: the Go equivalents of the Celery control ping and
                   the Beat heartbeat (milestone A).
  4 superuser      `bin/assozeta create-superuser --noinput` (password by DJANGO_SUPERUSER_PASSWORD)
                   works in Go mode and the new administrator signs in through the Go API (milestone A).
  5 backup/restore accepted work queued on the Go side (a mail task due next month) and a settings
                   value are backed up; after a later settings write, `bin/assozeta restore` brings
                   back the value, keeps Go effective with a newer generation, re-owns the work on Go
                   and never starts a Django resident service (milestone A). This replaces the
                   production smoke's packaged Go probe for a Go-mode installation.
  6 failing image  with ASSOZETA_BACKEND_GO_REF pointing at an image that cannot start, `restart`
                   fails, keeps maintenance on and never falls back to a Django service; with the
                   reference restored, `restart` brings the Go set back (milestone A).
  7 independence   the container event history since Go mode began (lanes 1-6) holds no container of
                   the Django backend image, one-shot migrators included (milestone B).

Images: ASSOZETA_HANDOFF_P5_IMAGES json if given; else the images the Phase 5 lane already built for
this revision (tags assozeta-p5-<name>:<revision>, reused, never rebuilt: a rebuild after the Phase 5
lane would send its root-owned leftovers under quality-reports/ as build context); else built once
from the checkout as Phase 5 does. The install root lives in the system temporary directory (or
ASSOZETA_HANDOFF_P6_WORKDIR), never under the repository, so this lane leaves nothing in a later
build context either. Cost: the image builds if any, then ~10 min of lanes.
Run: python3 -B -m unittest -v TESTS.handoff.test_p6_installed
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid

from TESTS.handoff import p5_harness as h5
from TESTS.handoff import test_p5_installed as p5i
from TESTS.handoff.topology import ROOT

RED_A, RED_B = 'P6A contract red', 'P6B contract red'
STATE = {}
OUTPUT = ROOT / 'quality-reports/handoff/p6/installed'


def watch(installed):
    """Stream this installation's container events into a file from now until tearDownModule.

    A one-off `docker events --since` replays only the daemon's short in-memory history (health-check
    exec events alone can push a lifecycle command's events out of it), so the Go-mode history is
    recorded live instead."""
    path = STATE['work'] / 'events.jsonl'
    STATE['events_file'] = path
    STATE['watcher'] = subprocess.Popen(
        ['docker', 'events', '--filter', f'label=com.docker.compose.project={installed.project}', '--filter',
         'type=container', '--format', '{{json .}}'], stdout=path.open('w'), stderr=subprocess.DEVNULL)


def recorded(since=0.0):
    """Container events recorded by watch() at or after `since` (epoch seconds)."""
    found = []
    path = STATE.get('events_file')
    for line in (path.read_text().splitlines() if path and path.exists() else []):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        attributes = event.get('Actor', {}).get('Attributes', {})
        moment = event.get('timeNano', 0) / 1e9
        if moment >= since:
            found.append({'action': event.get('Action') or event.get('status'), 'time': moment,
                          'service': attributes.get('com.docker.compose.service'), 'image': attributes.get('image'),
                          'oneoff': attributes.get('com.docker.compose.oneoff') == 'True'})
    return found


def settle(quiet=5, limit=180):
    """Wait until the event stream has delivered what the last command caused (no new event for
    `quiet` seconds, at most `limit`): a loaded daemon delivers events late."""
    path = STATE.get('events_file')
    size, still, deadline = -1, 0, time.monotonic() + limit
    while path and time.monotonic() < deadline and still < quiet:
        time.sleep(1)
        current = path.stat().st_size if path.exists() else 0
        still = still + 1 if current == size else 0
        size = current


def phase5_tags(log):
    """{name: tag} of the images test_p5_installed builds for this revision (p5i.images naming)."""
    revision = p5i.run(['git', '-C', ROOT, 'rev-parse', '--short=12', 'HEAD'], log=log).stdout.strip()
    return {name: f'assozeta-p5-{name}:{revision}' for name in ('backend', 'backend-go', 'web', 'renderer', 'updater')}


def images(log):
    if os.environ.get('ASSOZETA_HANDOFF_P5_IMAGES'):
        return p5i.images(log)
    tags = phase5_tags(log)
    present = [p5i.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], log=log, check=False).returncode == 0
               for tag in tags.values()]
    if all(present):
        STATE['images'] = 'reused from the Phase 5 lane'
        return tags
    STATE['images'] = 'built'
    return p5i.images(log)


def setUpModule():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    STATE['log'] = log = (OUTPUT / 'lanes.log').open('w')
    p5i.STATE['log'] = log
    work = Path(os.environ.get('ASSOZETA_HANDOFF_P6_WORKDIR') or tempfile.gettempdir()).resolve()
    work.mkdir(parents=True, exist_ok=True)
    STATE['work'] = Path(tempfile.mkdtemp(prefix='assozeta-p6-installed-', dir=work))
    try:
        installed = p5i.Installed(STATE['work'], images(log), log)
        STATE['installed'] = installed
        installed.install()
        runtime, generation, _, _ = installed.authority()
        outcome = installed.psql(f"SELECT outcome FROM public.assozeta_commit_runtime_authority('{uuid.uuid4()}', "
                                 f"'{runtime}', {generation}, 'go');")
        if outcome != 'committed':
            raise h5.HarnessError(f'fixture authority commit answered {outcome}')
        watch(installed)
        settle()
        STATE['go_since'] = time.time()
        STATE['restart'] = installed.cli('restart', check=False)
    except BaseException:
        tearDownModule()
        raise


def tearDownModule():
    installed = STATE.get('installed')
    watcher = STATE.get('watcher')
    if watcher and watcher.poll() is None:
        watcher.terminate()
        watcher.wait(30)
    try:
        if installed:
            installed.uninstall()
    finally:
        if STATE.get('work'):
            shutil.rmtree(STATE['work'], ignore_errors=True)
        if STATE.get('log') and not STATE['log'].closed:
            STATE['log'].close()


class GoModeInstalled(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.inst = STATE['installed']

    def go_set(self, label, red=RED_A):
        running = self.inst.running()
        self.assertEqual(sorted(running - h5.SHARED), sorted(h5.GO_APP), f'{red}: application services {label}')
        self.assertTrue(h5.SHARED <= running, f'{red}: shared services stopped {label}')

    def django_residents_started(self, since):
        settle()
        return [e for e in recorded(since) if e['action'] in ('create', 'start', 'restart')
                and e['service'] in h5.DJANGO_APP]

    def health(self):
        result = self.inst.cli('health', check=False)
        lines = [line for line in result.stdout.strip().splitlines() if line.strip().startswith('{')]
        try:
            return result.returncode, json.loads(lines[-1]) if lines else None
        except ValueError:
            return result.returncode, None

    def test_1_go_mode_lifecycle_runs_only_go(self):
        restart = STATE['restart']
        self.assertEqual(restart.returncode, 0, f'{RED_A}: restart in Go mode failed: {restart.stdout[-500:]}')
        self.go_set('after the Go-mode restart')
        self.assertEqual(self.django_residents_started(STATE['go_since']), [],
                         f'{RED_A}: a Django resident service started in Go mode')
        self.assertEqual(self.inst.http('GET', '/api/readyz')[0], 200, f'{RED_A}: the Go API is not ready')
        code, _ = self.inst.http('POST', '/api/profile/settings/tables', self.inst.token,
                                 {'tables_settings': {'p6': {'columns': ['name'], 'written_by': 'go'}}})
        self.assertIn(code, (200, 201, 204), f'{RED_A}: a settings write through the Go API failed: {code}')

    def test_2_go_mode_health(self):
        code, document = self.health()
        self.assertIsNotNone(document, f'{RED_A}: bin/assozeta health printed no JSON document (exit {code})')
        checks = document.get('checks') or {}
        self.assertEqual((code, document.get('runtime'), {k: v.get('status') for k, v in checks.items()}),
                         (0, 'go', dict.fromkeys(('routing', 'schema', 'worker', 'scheduler', 'relay'), 'passed')),
                         f'{RED_A}: health of the running Go-mode installation')

    def test_3_go_mode_owner_diagnostics(self):
        status, diagnostics = self.inst.http('POST', '/api/instance/admin/diagnostics', self.inst.token, {})
        self.assertEqual(status, 200, f'{RED_A}: owner diagnostics in Go mode: HTTP {status}')
        found = {check['id']: (check['status'], check.get('message')) for check in (diagnostics or {}).get('checks', [])}
        self.assertEqual({key: (found.get(key) or (None,))[0] for key in ('worker', 'scheduler')},
                         {'worker': 'passed', 'scheduler': 'passed'},
                         f'{RED_A}: Go-mode diagnostics need equivalents of the Celery ping and the Beat heartbeat: '
                         f'{ {key: found.get(key) for key in ("worker", "scheduler")} }')

    def test_4_go_mode_superuser_creation(self):
        email = 'admin-p6@example.invalid'
        password = 'P6-' + uuid.uuid4().hex + '!a'
        environment = {key: value for key, value in os.environ.items() if not key.startswith('ASSOZETA_')}
        environment.update(ASSOZETA_ENV_FILE=str(self.inst.env_file), DJANGO_SUPERUSER_PASSWORD=password,
                           PATH=f'{self.inst.tools}{os.pathsep}{os.environ["PATH"]}')
        p5i.STATE.setdefault('secrets', []).append(password)
        started = time.time()
        result = p5i.run([self.inst.root / 'bin/assozeta', 'create-superuser', '--username', 'admin-p6', '--email', email,
                          '--first-name', 'Admin', '--last-name', 'Fixture', '--noinput'], log=self.inst.log, check=False,
                         cwd=self.inst.root, env=environment, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, f'{RED_A}: create-superuser in Go mode: {result.stdout[-400:]}')
        status, _ = self.inst.http('POST', '/api/oauth2/login', body={'username': email, 'password': password})
        self.assertEqual(status, 200, f'{RED_A}: the administrator created in Go mode cannot sign in')
        self.go_set('after create-superuser')

    def test_5_go_mode_backup_and_restore_keep_go_and_the_work(self):
        inst = self.inst
        before_value = {'p6': {'columns': ['name'], 'written_by': 'before-backup'}}
        after_value = {'p6': {'columns': ['name'], 'written_by': 'after-backup'}}

        def table_settings():
            # GET /api/profile/settings answers {'settings': {..., 'table_settings': ...}} (singular).
            code, body = inst.http('GET', '/api/profile/settings', inst.token)
            return code, (((body or {}).get('settings') or {}).get('table_settings') or {}).get('p6')

        self.assertIn(inst.http('POST', '/api/profile/settings/tables', inst.token, {'tables_settings': before_value})[0],
                      (200, 201, 204), f'{RED_A}: the Go-mode installation does not serve a settings write before '
                                       'the backup (Go mode never became healthy)')
        self.assertEqual(table_settings(), (200, before_value['p6']),
                         f'{RED_A}: the Go API does not read back its own settings write before the backup')
        task = inst.seed('assozeta-go', h5.MAIL_TASK, eta=h5.future(30), args=h5.mail_args(6))
        archived_generation = inst.authority()[1]
        backup = inst.cli('backup', check=False)
        self.assertEqual(backup.returncode, 0, f'{RED_A}: backup in Go mode: {backup.stdout[-400:]}')
        archive = sorted((inst.root / 'backups').glob('assozeta-*.tar.gz'))[-1]
        inst.http('POST', '/api/profile/settings/tables', inst.token, {'tables_settings': after_value})
        self.assertEqual(table_settings(), (200, after_value['p6']),
                         f'{RED_A}: the Go API does not read back the settings written after the backup')
        started = time.time()
        restore = inst.cli('restore', str(archive), '--yes', check=False)
        self.assertEqual(restore.returncode, 0, f'{RED_A}: restore in Go mode: {restore.stdout[-600:]}')
        runtime, generation, _, _ = inst.authority()
        self.assertEqual(runtime, 'go', f'{RED_A}: the restore left Go mode')
        self.assertGreater(generation, archived_generation, f'{RED_A}: the restore rewound the generation')
        self.go_set('after the restore')
        self.assertEqual(self.django_residents_started(started), [], f'{RED_A}: the restore started a Django service')
        row = inst.ledger(task)
        self.assertTrue(row and row[1] == 'go' and int(row[2]) == generation and row[0] in ('pending', 'running'),
                        f'{RED_A}: the archived Go work is not owned by Go after the restore: {row}')
        self.assertFalse(inst.copies(task).get('celery'), f'{RED_A}: archived Go work moved to the Django queue')
        self.assertEqual(table_settings(), (200, before_value['p6']),
                         f'{RED_A}: the Go API does not read back the restored data')

    def test_6_go_mode_failing_go_image_never_falls_back_to_django(self):
        inst = self.inst
        reference = inst.env()['ASSOZETA_BACKEND_GO_REF']
        started = time.time()
        inst.set_env(ASSOZETA_BACKEND_GO_REF='assozeta-p6-missing/backend-go:absent')
        try:
            failed = inst.cli('restart', check=False)
            self.assertNotEqual(failed.returncode, 0, f'{RED_A}: restart with an unusable Go image reported success')
            self.assertTrue((inst.root / '.operations/maintenance.flag').exists(),
                            f'{RED_A}: maintenance must stay on while the Go image cannot start')
            self.assertEqual(self.django_residents_started(started), [],
                             f'{RED_A}: a failing Go image made the lifecycle start Django')
        finally:
            inst.set_env(ASSOZETA_BACKEND_GO_REF=reference)
        recovered = inst.cli('restart', check=False)
        self.assertEqual(recovered.returncode, 0, f'{RED_A}: restart with the Go image restored: {recovered.stdout[-400:]}')
        self.go_set('after recovering the Go image')

    def test_7_go_mode_history_has_no_django_container(self):
        image = self.inst.images['backend']
        settle()
        django = [e for e in recorded(STATE['go_since']) if e['action'] in ('create', 'start')
                  and (e['image'] == image or e['service'] in h5.DJANGO_APP or e['service'] == 'migrate')]
        self.assertEqual(django, [], f'{RED_B}: Go-mode lifecycle ran Django containers (one-shots included)')


if __name__ == '__main__':
    unittest.main()
