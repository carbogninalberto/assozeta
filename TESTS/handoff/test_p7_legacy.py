"""Phase 7 part C2: a published mixed release upgraded into each steady mode (CONTRACT P7 v1).

Release matrix lane `legacy_mixed_upgrade`. Source: the latest published stable release before the
switch, v1.0.48 (dual-API distribution), installed from its own files (`git archive v1.0.48 selfhost`,
the content of its published bundle) with its own CLI and its published GHCR images
(`ASSOZETA_VERSION=1.0.48`, official repositories, so the release supports managed self-update), then
configured the way existing mixed installations run: Go task routing on (ASSOZETA_GO_TASKS=1,
GO_TASK_QUEUE_TASKS for the mail tasks, the instance's go_tasks_enabled), the old relay and protocol
selectors, and delayed work already on both historical queues (`celery` and `assozeta-go`, due next
month, so no old worker can finish it before the upgrade).

Target: this commit's images (the candidate's in the release workflow, ASSOZETA_HANDOFF_P5_IMAGES),
published as release v<ASSOZETA_RELEASE_VERSION> with the candidate application images unchanged (release
job), or as v9.9.9 with backend/backend-go restamped to that version (local/PR builds carry VERSION=test),
through the Phase 6 test transport (selfhost/tests/fixtures: the updater
image gains the transport, which serves releases.json, the bundle of this checkout's selfhost/ and
its assozeta-update.json from <installation>/.fixtures; local image ids are the allowlisted
references). The operator runs the SOURCE CLI exactly as documented: `./bin/assozeta upgrade 9.9.9`
(bootstrap of the verified target CLI in the target updater image; adoption preflight, backup,
migrations, adoption, start).

  into Go      instance ui_api_version v2: the upgrade reports '@@ASSOZETA_RUNTIME_ADOPTION' with
               requested_runtime go and exits 0; the authority is go; exactly the Go application
               services run, on the target image; the legacy knobs are retired (absent, or
               GO_TASK_QUEUE_TASKS empty); every seeded task is owned by go in the ledger with no copy
               left on the Django queue; status (runner socket) effective = intended = go, no
               maintenance; public readiness answers; the instance setting is kept.
  into Django  instance ui_api_version v1 (same mixed knobs): the same with django and the Go queue.
Each lane then passes its observations (services, work, inactive runtime) through the release gate
(tools/release/qualify.py, lane legacy_mixed_upgrade) and writes them to
$ASSOZETA_RELEASE_OBSERVATIONS/legacy_mixed_upgrade-<mode>.json when set (the release receipt).

The gate is loaded first, before any image is built or pulled. Cost per lane: pulling the v1.0.48
images, an install and an upgrade (~25 min); CI only (handoff-contracts.yml 'installed').

Run: python3 -B -m unittest -v TESTS.handoff.test_p7_legacy
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import uuid

from TESTS.handoff import p5_harness as h
from TESTS.handoff import p7_release as r
from TESTS.handoff import test_p5_installed as p5
from TESTS.handoff.test_p7_installed import now
from TESTS.handoff.topology import ROOT

RED = r.RED
SOURCE = 'v1.0.48'
# The release job sets the candidate's own version (its images carry it, so they are used unchanged);
# local and PR builds carry VERSION=test, which is not a stable release, and are restamped as 9.9.9.
TARGET = os.environ.get('ASSOZETA_RELEASE_VERSION') or '9.9.9'
RESTAMP = not os.environ.get('ASSOZETA_RELEASE_VERSION')
OUTPUT = ROOT / 'quality-reports/handoff/p7/legacy'
MIXED = {'ASSOZETA_GO_TASKS': '1',
         'GO_TASK_QUEUE_TASKS': 'core.tasks.send_mail_async,send_email_text,send_email_template,send_email_html',
         'ASSOZETA_REALTIME_RELAY_BACKEND': 'django', 'ASSOZETA_DJANGO_RELAY_REPLICAS': '1',
         'ASSOZETA_GO_RELAY_REPLICAS': '0', 'ASSOZETA_BAKNEY_PROTOCOL_BACKEND': 'django',
         'ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM': 'api:8000'}
RETIRED = tuple(key for key in MIXED if key != 'GO_TASK_QUEUE_TASKS')
STATE = {}


def setUpModule():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    STATE['log'] = (OUTPUT / 'lanes.log').open('w')
    work = Path(os.environ.get('ASSOZETA_HANDOFF_P7_WORKDIR') or OUTPUT)
    work.mkdir(parents=True, exist_ok=True)
    STATE['work'] = Path(tempfile.mkdtemp(prefix='legacy-', dir=work))
    STATE['tags'] = []


def tearDownModule():
    try:
        for tag in STATE.get('tags', []):
            p5.run(['docker', 'image', 'rm', '-f', tag], log=STATE['log'], check=False)
        for tag, identifier in STATE.get('retag', {}).items():
            p5.run(['docker', 'tag', identifier, tag], log=STATE['log'], check=False)
    finally:
        if STATE.get('work'):
            shutil.rmtree(STATE['work'], ignore_errors=True)
        (OUTPUT / 'observations.json').write_text(json.dumps(STATE.get('observations', {}), indent=1))
        if STATE.get('log') and not STATE['log'].closed:
            STATE['log'].close()


def candidate_images():
    if 'candidate' not in STATE:
        p5.STATE['log'] = STATE['log']
        STATE['candidate'] = p5.images(STATE['log'])
    return STATE['candidate']


def build(directory, dockerfile, tag):
    (directory / 'Dockerfile').write_text(dockerfile)
    p5.run(['docker', 'build', '-t', tag, directory], log=STATE['log'], timeout=1800)
    STATE['tags'].append(tag)
    return p5.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], log=STATE['log']).stdout.strip()


class LegacyInstalled(p5.Installed):
    """The v1.0.48 distribution, installed from its own files with its published images."""

    def __init__(self, work, log):
        self.work, self.log, self.images = work, log, {}
        archive = subprocess.run(['git', '-C', ROOT, 'archive', SOURCE, 'selfhost'], check=True,
                                 capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as release:
            release.extractall(work, filter='data')
        self.root = work / 'selfhost'
        self.env_file = self.root / '.env'
        self.tools = work / 'tools'
        self.tools.mkdir()
        real = shutil.which('docker')
        # Host-side registry stand-in for the one image the source CLI pulls by tag (the target updater,
        # tagged locally); every other docker call is the real CLI.
        (self.tools / 'docker').write_text(f'#!/bin/sh\nif [ "$1" = pull ] && "{real}" image inspect "$2" >/dev/null 2>&1; '
                                           f'then exit 0; fi\nexec "{real}" "$@"\n')
        (self.tools / 'docker').chmod(0o755)
        if not shutil.which('flock'):
            (self.tools / 'flock').write_text(f'#!/bin/sh\nexec "{shutil.which("python3")}" -c "import fcntl,sys; '
                                              f'fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX)" "$@"\n')
            (self.tools / 'flock').chmod(0o755)
        self.project = 'assozeta-p7l-' + uuid.uuid4().hex[:8]
        self.port = h.free_port()
        self.base = f'http://localhost:{self.port}'

    def install(self):
        self.cli('configure', '--domain', f'localhost:{self.port}', '--version', SOURCE[1:])
        p5.STATE['secrets'] = sorted({value for key, value in self.env().items() if len(value) >= 16 and any(
            word in key for word in ('PASSWORD', 'SECRET', 'TOKEN', 'KEY'))} | {p5.OWNER_PASSWORD}, key=len, reverse=True)
        self.set_env(COMPOSE_PROJECT_NAME=self.project, HTTP_PORT=self.port, HTTPS_PORT=h.free_port())
        self.cli('install', timeout=3600)
        values = self.env()
        code, _ = self.http('POST', '/api/instance/configure', headers={'X-Setup-Token': values['INSTANCE_SETUP_TOKEN']},
                            body={'domain': f'localhost:{self.port}',
                                  'oem': {'name': 'P7 Club', 'abbreviation': 'P7', 'primaryColor': '#351DC2',
                                          'supportEmail': p5.OWNER_EMAIL, 'logo': ''}, 'oauth': {}, 'stripe': {},
                                  'initialization': {'type': 'fresh', 'associationName': 'P7 Association',
                                                     'ownerEmail': p5.OWNER_EMAIL, 'ownerPassword': p5.OWNER_PASSWORD}})
        if code != 200:
            raise h.HarnessError(f'v1.0.48 first-run setup failed: HTTP {code}')
        code, login = self.http('POST', '/api/oauth2/login', body={'username': p5.OWNER_EMAIL,
                                                                   'password': p5.OWNER_PASSWORD})
        if code != 200:
            raise h.HarnessError(f'v1.0.48 owner login failed: HTTP {code}')
        self.token, self.refresh = login['access_token'], login.get('refresh_token')

    def uninstall(self):
        super().uninstall()
        compose = ['docker', 'compose', '--project-name', self.project, '-f', self.root / 'compose.yml',
                   '--env-file', self.env_file, '--profile', '*', 'down', '--volumes', '--remove-orphans']
        if self.env_file.exists():
            p5.run(compose, log=self.log, check=False, env={**os.environ, 'ASSOZETA_ENV_FILE': str(self.env_file),
                                                            'ASSOZETA_SELFHOST_DIR': str(self.root)})


class LegacyMixedUpgrade(r.ContractCase, unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.gate = r.load_gate(self)
        self.work = Path(tempfile.mkdtemp(prefix=self._testMethodName[-12:] + '-', dir=STATE['work']))
        self.inst = LegacyInstalled(self.work, STATE['log'])
        self.addCleanup(self.inst.uninstall)

    # -- the published target (test transport) ----------------------------------------------------------------
    def publish_target(self):
        """Release v9.9.9 = this checkout's distribution and the candidate images, served from .fixtures."""
        sys.path[:0] = [p for p in (str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')) if p not in sys.path]
        from distribution import digest, snapshot_managed
        candidate = candidate_images()
        fixtures = self.inst.root / '.fixtures'
        fixtures.mkdir()
        context = self.work / 'image-context'
        context.mkdir()
        shutil.copytree(ROOT / 'selfhost/tests/fixtures', context / 'transport',
                        ignore=shutil.ignore_patterns('__pycache__'))
        name = self.inst.project
        updater = build(context, f'''FROM {candidate['updater']}
COPY transport /test-transport
RUN mv /usr/local/bin/docker /usr/local/bin/docker-real && cp /test-transport/docker /usr/local/bin/docker && chmod +x /usr/local/bin/docker
ENV PYTHONPATH=/test-transport:/runner ASSOZETA_TEST_FIXTURES={fixtures} RUNNER_VERSION={TARGET} ASSOZETA_READINESS_ATTEMPTS=5 ASSOZETA_READINESS_DELAY=1
ENTRYPOINT ["python3", "/test-transport/entrypoint.py"]
''', f'{name}-updater:{TARGET}')
        inspect = lambda ref: p5.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', ref],
                                     log=STATE['log']).stdout.strip()
        if RESTAMP:
            backend = build(context, f'''FROM {candidate['backend']}
USER root
RUN printf '{TARGET}\\n' > /app/VERSION
USER assozeta
''', f'{name}-backend:{TARGET}')
            backend_go = build(context, f'''FROM {candidate['backend-go']}
USER root
RUN printf '{TARGET}\\n' > /app/VERSION
USER 10001:10001
''', f'{name}-backend-go:{TARGET}')
        else:
            # The exact candidate application images; only the updater gains the test transport layer.
            backend, backend_go = inspect(candidate['backend']), inspect(candidate['backend-go'])
        web, renderer = inspect(candidate['web']), inspect(candidate['renderer'])
        tag = f'ghcr.io/carbogninalberto/assozeta-updater:{TARGET}'
        previous = p5.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], log=STATE['log'],
                          check=False).stdout.strip()
        p5.run(['docker', 'tag', updater, tag], log=STATE['log'])
        if tag in STATE['tags'] or tag in STATE.setdefault('retag', {}):
            pass  # an earlier lane already recorded how to restore this tag
        elif previous.startswith('sha256:'):
            STATE['retag'][tag] = previous  # the release job's own tag of the candidate updater
        else:
            STATE['tags'].append(tag)
        (fixtures / 'images.json').write_text(json.dumps({
            'fixture-backend': backend, 'fixture-backend-go': backend_go, 'fixture-web': web,
            'fixture-renderer': renderer, tag: updater}))
        source = self.work / 'target-selfhost'
        shutil.copytree(ROOT / 'selfhost', source, ignore=shutil.ignore_patterns(
            '.env', '.env.dev', '.updater', '.lifecycle*', '.restore-*', 'backups', '__pycache__', 'node_modules',
            'test-results'))
        files = {name: (source / name).read_bytes() for name in snapshot_managed(source)}
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for path, content in files.items():
                member = tarfile.TarInfo(f'selfhost/{path}')
                member.size, member.mode = len(content), (source / path).stat().st_mode & 0o777
                archive.addfile(member, io.BytesIO(content))
        bundle = stream.getvalue()
        release = fixtures / f'v{TARGET}'
        release.mkdir()
        bundle_name = f'assozeta-selfhost-v{TARGET}.tar.gz'
        (release / bundle_name).write_bytes(bundle)
        (release / 'assozeta-update.json').write_text(json.dumps({
            'schema_version': 1, 'version': TARGET, 'bundle_sha256': digest(bundle),
            'images': {'backend': backend, 'web': web, 'renderer': renderer, 'updater': updater},
            'extensions': {'go_backend': {'schema_version': 1, 'image': backend_go}},
            'files': {path: digest(content) for path, content in files.items()}}))
        (fixtures / 'releases.json').write_text(json.dumps([{
            'id': 999, 'tag_name': f'v{TARGET}', 'name': f'v{TARGET}', 'published_at': '2026-11-24T00:00:00Z',
            'body': 'Release candidate under qualification', 'draft': False, 'prerelease': False,
            'assets': [{'name': item, 'state': 'uploaded', 'size': (release / item).stat().st_size}
                       for item in (bundle_name, 'assozeta-update.json')]}]))
        return {'backend': backend, 'backend-go': backend_go}

    # -- the mixed source ---------------------------------------------------------------------------------------
    def mixed_source(self, ui_api_version):
        inst = self.inst
        inst.install()
        inst.psql(f"UPDATE public.instance_instanceconfiguration SET ui_api_version='{ui_api_version}', "
                  'go_tasks_enabled=true;')
        inst.set_env(**MIXED)
        inst.cli('restart', timeout=3600)
        running = inst.running()
        if not {'api', 'api-go', 'worker', 'worker-go'} <= running:
            raise h.HarnessError(f'the v1.0.48 mixed fixture is not running both runtimes: {sorted(running)}')
        seeded = {}
        for queue, task, args in (('celery', h.MAIL_TASK, h.mail_args(71)), ('celery', h.DB_TASK, ()),
                                  ('assozeta-go', h.MAIL_TASK, h.mail_args(72))):
            task_id = str(uuid.uuid4())
            inst.redis('LPUSH', queue, payload=h.envelope(task, task_id, args=args, queue=queue, eta=h.future(30)))
            seeded[task_id] = queue
        return seeded

    def upgrade_into(self, target, ui_api_version):
        inst = self.inst
        seeded = self.mixed_source(ui_api_version)
        images = self.publish_target()
        started = time.time()
        result = inst.cli('upgrade', TARGET, check=False, timeout=5400)
        self.assertEqual(result.returncode, 0, f'./bin/assozeta upgrade {TARGET} from the mixed {SOURCE} '
                                               f'(instance {ui_api_version}): {result.stdout[-800:]}')
        reports = [line.split(' ', 1)[1] for line in result.stdout.splitlines()
                   if line.startswith('@@ASSOZETA_RUNTIME_ADOPTION ')]
        self.assertTrue(reports, 'the upgrade printed no @@ASSOZETA_RUNTIME_ADOPTION preflight')
        self.assertEqual(json.loads(reports[-1]).get('requested_runtime'), target,
                         f'instance setting {ui_api_version} must request {target}')
        self.assertEqual(inst.authority()[0], target, f'authority after the upgrade into {target}')
        app, other = (h.GO_APP, h.DJANGO_APP) if target == 'go' else (h.DJANGO_APP, h.GO_APP)
        self.assertEqual(sorted(inst.running() - h.SHARED), sorted(app), f'application services after the upgrade '
                                                                          f'into {target}')
        image = images['backend-go' if target == 'go' else 'backend']
        for container in inst.containers():
            if container['service'] in app:
                self.assertEqual(container['image'], image, f'{container["service"]} is not on the target image')
        env = inst.env()
        self.assertEqual(sorted(key for key in RETIRED if key in env), [], 'legacy knobs not retired')
        self.assertEqual(env.get('GO_TASK_QUEUE_TASKS', ''), '', 'GO_TASK_QUEUE_TASKS not retired')
        self.assertEqual(inst.psql('SELECT ui_api_version FROM public.instance_instanceconfiguration ORDER BY id '
                                   'LIMIT 1;'), ui_api_version, 'the instance setting must be kept')
        items = []
        for task_id in seeded:
            row = inst.ledger(task_id)
            self.assertTrue(row and row[1] == target, f'delayed work {task_id} from {seeded[task_id]} is not owned by '
                                                      f'{target} after the upgrade: {row}')
            self.assertFalse(inst.copies(task_id).get(h.QUEUE['django' if target == 'go' else 'go']),
                             f'delayed work {task_id} left on the inactive queue')
            items.append({'id': task_id, 'state': row[0], 'owner': row[1], 'eta_future': True})
        code, status = inst.runtime_status()
        self.assertEqual((code, (status or {}).get('effective_runtime'), (status or {}).get('intended_runtime'),
                          (status or {}).get('maintenance')), (200, target, target, False),
                         'runner status after the upgrade')
        self.assertEqual(inst.http('GET', '/api/readyz')[0], 200, f'{target} does not serve after the upgrade')
        late = sorted({e['service'] for e in inst.events(time.time() - 60)
                       if e['action'] in ('create', 'start', 'restart') and e['service'] in other})
        observations = [{'kind': 'services', 'runtime': target, 'running': sorted(inst.running())},
                        {'kind': 'work', 'runtime': target, 'items': items},
                        {'kind': 'inactive_runtime', 'runtime': target, 'started': late,
                         'running': sorted(inst.running() & other), 'requests': 0}]
        self.assert_gate_accepts(observations, started)

    def assert_gate_accepts(self, observations, started):
        lane = 'legacy_mixed_upgrade'
        commit = subprocess.run(['git', '-C', ROOT, 'rev-parse', 'HEAD'], capture_output=True, text=True,
                                check=True).stdout.strip()
        fixtures = {tree: subprocess.run(['git', '-C', ROOT, 'rev-parse', f'HEAD:{tree}'], capture_output=True,
                                         text=True, check=True).stdout.strip() for tree in r.FIXTURE_TREES}
        images = {}
        for name in r.IMAGES:
            identifier = p5.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', candidate_images()[name]],
                                log=STATE['log']).stdout.strip()
            images[name] = f'local.invalid/assozeta-{name}@{identifier}'
        cand = {'format': 1, 'commit': commit, 'version': None, 'images': images, 'fixtures': fixtures,
                'bundle_sha256': hashlib.sha256(commit.encode()).hexdigest()}
        receipt = {'format': 1, 'lane': lane, 'commit': commit, 'images': images, 'bundle_sha256': cand['bundle_sha256'],
                   'fixtures': fixtures, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(started)),
                   'finished_at': now(), 'run': 'installed-p7-legacy', 'tests': {self.id(): 'passed'},
                   'observations': observations}
        spec = {'tests': [self.id()], 'open': None,
                'observations': [{'kind': o['kind'], 'runtime': o['runtime']} for o in observations]}
        with tempfile.TemporaryDirectory() as directory:
            result = self.gate.evaluate(cand, [receipt], matrix={'format': 1, 'lanes': {lane: spec}},
                                        expected_red=r.closed_expected_red(), ledger=r.closed_ledger(directory),
                                        root=Path(directory))
        self.assertEqual(result['reasons'], [], 'the release gate refuses the upgraded installation')
        mode = observations[0]['runtime']
        STATE.setdefault('observations', {})[mode] = observations
        target = os.environ.get('ASSOZETA_RELEASE_OBSERVATIONS')
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / f'{lane}-{mode}.json').write_text(json.dumps({'lane': lane, 'observations': observations},
                                                                         indent=1))

    def test_mixed_release_upgraded_into_go(self):
        self.upgrade_into('go', 'v2')

    def test_mixed_release_upgraded_into_django(self):
        self.upgrade_into('django', 'v1')


if __name__ == '__main__':
    unittest.main()
