"""Phase 7 part C: installed release lanes and installed mutation controls (CONTRACT P7 v1).

A real installation of this commit's five images, exactly as Phase 5's installed lanes build and
install it (TESTS/handoff/test_p5_installed.Installed: distribution CLI, updater runner, real Compose,
PostgreSQL, Redis, MinIO, renderer, Caddy, an owner from first-run setup). In the release workflow
the images are the candidate's (ASSOZETA_HANDOFF_P5_IMAGES); in handoff-contracts they are built from
the checkout. Mail goes to a test SMTP sink on the installation's internal network (a stdlib Python
server run from the backend image, counting accepted messages by subject): the provider-side
counter the plan asks for.

Lanes (release matrix lanes in TESTS/handoff/release_matrix.json):
  1 steady state, both modes   in Django mode then in Go mode: running services; accepted work by
      identity (a database task in each runtime's own form - a Celery message in Django mode, a native
      durable job in Go mode - and a mail due now, a mail due next month) until the due work
      completes on the active runtime; a PDF probe (the renderer renders the active API through the
      internal alias active-api (its /healthz, served by both runtimes), as the application's documents do); the inactive runtime's
      container history and running set since the lane started; provider counts at the SMTP sink.
      The observations go through the release gate (tools/release/qualify.py evaluate, lane
      steady_state) and must be clean, and are written to quality-reports/handoff/p7/installed/
      observations.json (and $ASSOZETA_RELEASE_OBSERVATIONS/steady_state.json when set) for the
      release job's receipt.
  2/3 recovery without either app API   a switch to Go (then back to Django) is killed after the
      authority commit (controller held at activate:started, its container killed), then BOTH app
      APIs are stopped. The operator surfaces still work (runner status socket answers, public
      readiness does not) and `./bin/assozeta runtime-recover` alone converges to the target: status
      succeeded, maintenance off, exactly the target's application services, accepted work owned by
      the target.
  4-8 installed mutation controls (Go mode, each alone, then restored): a stopped worker-go, one
      task nobody can run, a renderer whose host allowlist misses active-api, the Django API started
      and called while Go is active, a second provider acceptance of one message. For each, a clean
      observation passes the gate first (control) and the mutated observation is refused with
      exactly its reason (missing_worker, unresolved_work, pdf_render_failed, inactive_runtime_call,
      duplicate_effect). The sixth mutation (a skipped release test) has no installed form: a skip is
      a violation for run_expected_red and test_not_passed for the gate (test_p7_gate).

Images: as Phase 6 (the Phase 5 lane's images reused; install root in the system temporary
directory). Cost: ~25 min of
lanes. CI only (handoff-contracts.yml 'installed'); never run locally on the shared machine.

Run: python3 -B -m unittest -v TESTS.handoff.test_p7_installed
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid

from TESTS.handoff import p5_harness as h
from TESTS.handoff import p7_release as r
from TESTS.handoff import test_p5_installed as p5
from TESTS.handoff import test_p6_installed as p6
from TESTS.handoff.topology import BUNDLE, ROOT

RED = r.RED
OUTPUT = ROOT / 'quality-reports/handoff/p7/installed'
STATE = {}
SMTP_PORT = 2525
SINK = r'''
import asyncio, json, time
OUT = '/sink/messages.jsonl'

async def handle(reader, writer):
    def send(text):
        writer.write((text + '\r\n').encode())
    send('220 p7-smtp ESMTP')
    await writer.drain()
    data, lines = False, []
    while True:
        raw = await reader.readline()
        if not raw:
            break
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if data:
            if line == '.':
                data, headers = False, {}
                for item in lines:
                    if not item:
                        break
                    if ':' in item and not item[0].isspace():
                        key, value = item.split(':', 1)
                        headers[key.strip().lower()] = value.strip()
                with open(OUT, 'a') as output:
                    output.write(json.dumps({'subject': headers.get('subject'), 'at': time.time()}) + '\n')
                lines = []
                send('250 OK queued')
            else:
                lines.append(line[1:] if line.startswith('.') else line)
        else:
            verb = line[:4].upper()
            if verb == 'EHLO':
                writer.write(b'250-p7-smtp\r\n250 8BITMIME\r\n')
            elif verb == 'DATA':
                data = True
                send('354 End data with <CR><LF>.<CR><LF>')
            elif verb == 'QUIT':
                send('221 Bye')
                await writer.drain()
                break
            else:
                send('250 OK')
        await writer.drain()
    writer.close()

async def main():
    server = await asyncio.start_server(handle, '0.0.0.0', 2525)
    async with server:
        await server.serve_forever()

asyncio.run(main())
'''
PDF_PROBE = r'''
const body = JSON.stringify({url: 'http://active-api:8000/healthz', type: 'pdf'});  // served by both runtimes
fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/render', {method: 'POST', body,
      headers: {'content-type': 'application/json'}})
  .then(async response => {
    const bytes = Buffer.from(await response.arrayBuffer());
    const pdf = bytes.subarray(0, 4).toString() === '%PDF';
    let reason = null;
    if (!pdf) { try { reason = JSON.parse(bytes.toString()).error || null; } catch (_) { reason = 'not a pdf'; } }
    console.log(JSON.stringify({status: response.status, pdf, reason: reason && String(reason).slice(0, 120)}));
  })
  .catch(error => console.log(JSON.stringify({status: 0, pdf: false, reason: String(error).slice(0, 120)})));
'''


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def git(*arguments):
    return subprocess.run(['git', '-C', ROOT, *arguments], check=True, capture_output=True, text=True).stdout.strip()


def candidate(installed):
    images = {}
    for name in r.IMAGES:
        identifier = p5.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', installed.images[name]],
                            log=installed.log).stdout.strip()
        images[name] = f'local.invalid/assozeta-{name}@{identifier}'
    bundle = hashlib.sha256()
    for name in sorted(BUNDLE):
        path = ROOT / 'selfhost' / name
        for item in sorted([path] if path.is_file() else path.rglob('*') if path.is_dir() else []):
            if item.is_file() and '__pycache__' not in item.parts:
                bundle.update(str(item.relative_to(ROOT)).encode() + b'\0' + item.read_bytes())
    return {'format': 1, 'commit': git('rev-parse', 'HEAD'), 'version': None, 'images': images,
            'bundle_sha256': bundle.hexdigest(),
            'fixtures': {tree: git('rev-parse', f'HEAD:{tree}') for tree in r.FIXTURE_TREES}}


class Sink:
    """The SMTP provider stub: counts accepted messages by subject."""

    def __init__(self, installed, work):
        self.installed, self.directory = installed, work / 'smtp'
        self.directory.mkdir()
        self.directory.chmod(0o777)
        (work / 'smtp-sink.py').write_text(SINK)
        self.script = work / 'smtp-sink.py'
        self.name = f'{installed.project}-p7-smtp'

    def start(self):
        p5.run(['docker', 'run', '-d', '--name', self.name, '--network', f'{self.installed.project}_internal',
                '--network-alias', 'p7-smtp', '--entrypoint', 'python', '-v', f'{self.directory}:/sink',
                '-v', f'{self.script}:/sink.py:ro', self.installed.images['backend'], '/sink.py'],
               log=self.installed.log)

    def stop(self):
        p5.run(['docker', 'rm', '-f', self.name], log=self.installed.log, check=False)

    def counts(self):
        path, counts = self.directory / 'messages.jsonl', {}
        for line in (path.read_text().splitlines() if path.exists() else []):
            subject = json.loads(line).get('subject')
            counts[subject] = counts.get(subject, 0) + 1
        return counts

    def deliver_again(self, subject):
        """Mutation: the provider accepts one message a second time."""
        script = ('import smtplib; s = smtplib.SMTP("127.0.0.1", 2525); '
                  f's.sendmail("sender@example.invalid", ["recipient@example.invalid"], "Subject: {subject}\\r\\n\\r\\nx"); '
                  's.quit()')
        p5.run(['docker', 'exec', self.name, 'python', '-c', script], log=self.installed.log)


def setUpModule():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    STATE['log'] = log = (OUTPUT / 'lanes.log').open('w')
    # Outside the repository: a later image build must not see this installation's root-owned files.
    work = Path(os.environ.get('ASSOZETA_HANDOFF_P7_WORKDIR') or tempfile.gettempdir()).resolve()
    work.mkdir(parents=True, exist_ok=True)
    STATE['work'] = Path(tempfile.mkdtemp(prefix='assozeta-p7-installed-', dir=work))
    try:
        p5.STATE['log'] = log
        # The Phase 5 lane's images for this revision, reused (a rebuild after it fails on its leftovers).
        installed = p5.Installed(STATE['work'], p6.images(log), log)
        STATE['installed'] = installed
        installed.install()
        sink = Sink(installed, STATE['work'])
        STATE['sink'] = sink
        sink.start()
        installed.set_env(EMAIL_HOST='p7-smtp', EMAIL_PORT=SMTP_PORT, EMAIL_USE_TLS='False', EMAIL_USE_SSL='False',
                          EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='')
        installed.cli('restart')
        STATE['candidate'] = candidate(installed)
    except BaseException:
        tearDownModule()
        raise


def tearDownModule():
    try:
        if STATE.get('sink'):
            STATE['sink'].stop()
        if STATE.get('installed'):
            STATE['installed'].uninstall()
    finally:
        if STATE.get('work'):
            shutil.rmtree(STATE['work'], ignore_errors=True)
        (OUTPUT / 'observations.json').write_text(json.dumps(STATE.get('observations', {}), indent=1))
        if STATE.get('log') and not STATE['log'].closed:
            STATE['log'].close()


class ReleaseLanes(r.ContractCase, unittest.TestCase):
    maxDiff = None
    request, follow, ensure = p5.InstalledLanes.request, p5.InstalledLanes.follow, p5.InstalledLanes.ensure

    def setUp(self):
        self.inst = STATE['installed']
        self.sink = STATE['sink']
        self.gate = r.load_gate(self)

    # -- observation ------------------------------------------------------------------------------------------------
    def compose_env(self):
        return {**os.environ, 'ASSOZETA_ENV_FILE': str(self.inst.env_file), 'ASSOZETA_SELFHOST_DIR': str(self.inst.root)}

    def compose_files(self, *extra):
        files = ['-f', self.inst.root / 'compose.yml']
        for path in extra:
            files += ['-f', path]
        return ['docker', 'compose', '--env-file', self.inst.env_file, *files]

    def mail(self, runtime, eta=None):
        subject = f'P7 {runtime} {uuid.uuid4().hex[:12]}'
        task = self.inst.seed(h.QUEUE[runtime], h.MAIL_TASK, eta=eta,
                              args=(subject, 'Synthetic body', 'sender@example.invalid', ['recipient@example.invalid']))
        return task, subject

    def database_task(self, runtime):
        """Work a database task, as each runtime's own producers create it: in Django mode a Celery message on
        `celery`; in Go mode a native durable job (the Go scheduler's form of the same task, run by the durable
        worker). A Celery envelope of it on the Go queue is not a Go-mode producer path; the Go consumer blocks it."""
        if runtime == 'django':
            return self.inst.seed(h.QUEUE['django'], h.DB_TASK)
        job = str(uuid.uuid4())
        self.inst.psql('INSERT INTO public.assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,'
                       f"max_attempts,available_at) VALUES ('{job}','00000000-0000-0000-0000-000000000000',"
                       f"'{h.DB_TASK}',1,'p7-{job}','{{}}',3,clock_timestamp());")
        return 'job:' + job

    def state(self, task_id):
        """(state, owner) of a Celery task in the ledger, or of a native Go durable job ('job:<id>')."""
        if task_id.startswith('job:'):
            state = self.inst.psql(f"SELECT state FROM public.assozeta_go_jobs WHERE id='{task_id[4:]}';")
            return (state, 'go') if state else None
        row = self.inst.ledger(task_id)
        return (row[0], row[1]) if row else None

    def work_items(self, runtime, seeded):
        items = []
        for task_id, future in seeded:
            state, owner = self.state(task_id) or ('absent', '')
            items.append({'id': task_id, 'state': state, 'owner': owner, 'eta_future': future})
        return items

    def services(self, runtime):
        return {'kind': 'services', 'runtime': runtime, 'running': sorted(self.inst.running())}

    def renders(self, runtime):
        result = p5.run([*self.compose_files(), 'exec', '-T', 'renderer', 'node', '-e', PDF_PROBE],
                        log=self.inst.log, check=False, env=self.compose_env())
        try:
            probe = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            probe = {'status': 0, 'pdf': False, 'reason': 'probe produced no result'}
        ok = probe.get('status') == 200 and probe.get('pdf')
        return {'kind': 'renders', 'runtime': runtime, 'renders': [
            {'document': 'pdf_probe', 'status': 'ok' if ok else 'failed', 'reason': None if ok else probe.get('reason')}]}

    def inactive(self, runtime, since):
        other = h.GO_APP if runtime == 'django' else h.DJANGO_APP
        started = sorted({e['service'] for e in self.inst.events(since)
                          if e['action'] in ('create', 'start', 'restart') and e['service'] in other})
        running = sorted(self.inst.running() & other)
        requests = 0
        for container in self.inst.containers():
            if container['service'] in other:
                logs = p5.run([*self.compose_files(), '--profile', '*', 'logs', '--no-color', container['service']],
                              log=self.inst.log, check=False, env=self.compose_env()).stdout
                requests += sum(1 for line in logs.splitlines() if ' /api/' in line)
        return {'kind': 'inactive_runtime', 'runtime': runtime, 'started': started, 'running': running,
                'requests': requests}

    def effects(self, runtime, subjects):
        counts = self.sink.counts()
        return {'kind': 'provider_effects', 'runtime': runtime,
                'effects': [{'provider': 'smtp', 'item': subject, 'count': counts.get(subject, 0)} for subject in subjects]}

    def wait_completed(self, task_ids, timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = [self.state(task_id) for task_id in task_ids]
            if all(row and row[0] == 'completed' for row in rows):
                return
            time.sleep(2)

    def wait_delivered(self, subjects, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not all(self.sink.counts().get(s) for s in subjects):
            time.sleep(2)

    # -- gate -----------------------------------------------------------------------------------------------------
    def verdict(self, observations, lane='steady_state'):
        spec = {'tests': [self.id()], 'open': None,
                'observations': [{'kind': o['kind'], 'runtime': o['runtime']} for o in observations]}
        cand = STATE['candidate']
        receipt = {'format': 1, 'lane': lane, 'commit': cand['commit'], 'images': dict(cand['images']),
                   'bundle_sha256': cand['bundle_sha256'], 'fixtures': dict(cand['fixtures']),
                   'started_at': STATE.get('started_at', now()), 'finished_at': now(), 'run': 'installed-p7',
                   'tests': {self.id(): 'passed'}, 'observations': observations}
        with tempfile.TemporaryDirectory() as directory:
            ledger = r.closed_ledger(directory)
            result = self.gate.evaluate(cand, [receipt], matrix={'format': 1, 'lanes': {lane: spec}},
                                        expected_red=r.closed_expected_red(), ledger=ledger, root=Path(directory))
        return sorted({reason['code'] for reason in result['reasons']}), result['reasons']

    def assert_clean(self, observations, label):
        codes, reasons = self.verdict(observations)
        self.assertEqual(codes, [], f'{label}: the gate refuses clean installed observations: {reasons}')

    def assert_refused(self, observations, code, label):
        codes, reasons = self.verdict(observations)
        self.assertEqual(codes, [code], f'{label}: the release gate must refuse with exactly {code}: {reasons}')

    # -- lanes ----------------------------------------------------------------------------------------------------
    def test_1_steady_state_observations_in_both_modes(self):
        STATE['started_at'] = now()
        collected = []
        for runtime in ('django', 'go'):
            self.ensure(runtime)
            since = time.time()
            due = self.database_task(runtime)
            mail, subject = self.mail(runtime)
            later, _ = self.mail(runtime, eta=h.future(30))
            self.wait_completed([due, mail])
            self.wait_delivered([subject])
            observations = [self.services(runtime),
                            {'kind': 'work', 'runtime': runtime,
                             'items': self.work_items(runtime, [(due, False), (mail, False), (later, True)])},
                            self.renders(runtime), self.inactive(runtime, since), self.effects(runtime, [subject])]
            self.assert_clean(observations, f'{runtime} mode steady state')
            collected += observations
        STATE['observations'] = {'lane': 'steady_state', 'observations': collected}
        target = os.environ.get('ASSOZETA_RELEASE_OBSERVATIONS')
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / 'steady_state.json').write_text(json.dumps(STATE['observations'], indent=1))

    def kill_after_commit(self, target):
        inst = self.inst
        # A fresh directory per switch: an arrival file left by an earlier switch must not satisfy this wait.
        barriers = inst.root / f'.p7-barriers-{uuid.uuid4().hex[:8]}'
        barriers.mkdir()
        (barriers / 'hold.activate:started.all').write_text('')
        environment = {key: value for key, value in os.environ.items() if not key.startswith('ASSOZETA_')}
        environment.update(ASSOZETA_ENV_FILE=str(inst.env_file), PATH=f'{inst.tools}{os.pathsep}{os.environ["PATH"]}',
                           ASSOZETA_HANDOFF_BARRIER_DIR=str(barriers))
        process = subprocess.Popen([str(inst.root / 'bin/assozeta'), 'runtime-switch', target], cwd=inst.root,
                                   env=environment, stdout=inst.log, stderr=subprocess.STDOUT)
        try:
            h.wait_for(lambda: list(barriers.glob('arrived.activate:started.*')) or process.poll() is not None,
                       'the controller to reach activate:started', timeout=1800, interval=0.5)
            self.assertTrue(list(barriers.glob('arrived.activate:started.*')),
                            f'the switch to {target} never reached activate:started (barrier seam)')
            helpers = p5.run(['docker', 'ps', '-q', '--filter',
                              f'label=org.assozeta.lifecycle.installation={inst.root}'], log=inst.log).stdout.split()
            self.assertTrue(helpers, 'no controller container alive at activate:started')
            p5.run(['docker', 'kill', *helpers], log=inst.log, check=False)
        finally:
            (barriers / 'hold.activate:started.all').unlink(missing_ok=True)
            process.wait(600)

    def recover_without_apis(self, source, target, seeded):
        inst = self.inst
        self.ensure(source)
        generation = inst.authority()[1]
        work = seeded()
        self.kill_after_commit(target)
        self.assertEqual(inst.authority()[:2], (target, generation + 1), 'the authority was committed before the kill')
        p5.run([*self.compose_files(), '--profile', 'django', '--profile', 'go', 'stop', 'api', 'api-go'],
               log=inst.log, env=self.compose_env())
        self.assertEqual(inst.running() & {'api', 'api-go'}, set(), 'fixture: both application APIs are stopped')
        code, status = inst.runtime_status()
        self.assertEqual(code, 200, 'the runner status socket must answer with neither application API up')
        self.assertIsNotNone(status, 'the runner status carries the runtime with neither application API up')
        self.assertNotEqual(inst.http('GET', '/api/readyz')[0], 200, 'fixture: no application API serves')
        result = inst.cli('runtime-recover', check=False)
        self.assertEqual(result.returncode, 0, f'runtime-recover with neither API up: {result.stdout[-600:]}')
        status = inst.runtime_status()[1] or {}
        self.assertEqual((status.get('effective_runtime'), status.get('intended_runtime'),
                          (status.get('operation') or {}).get('status'), status.get('maintenance')),
                         (target, target, 'succeeded', False), 'status after the recovery')
        app = h.GO_APP if target == 'go' else h.DJANGO_APP
        self.assertEqual(sorted(inst.running() - h.SHARED), sorted(app), 'application services after the recovery')
        self.assertEqual(inst.http('GET', '/api/readyz')[0], 200, f'{target} does not serve after the recovery')
        for task_id in work:
            row = inst.ledger(task_id)
            self.assertTrue(row and row[1] == target and int(row[2]) == generation + 1,
                            f'work accepted before the kill is not owned by {target}: {row}')

    def test_2_recovery_to_go_without_either_app_api(self):
        # Not due before the switch, so it is still accepted work when the controller is killed.
        self.recover_without_apis('django', 'go', lambda: [self.inst.seed('celery', h.DB_TASK, eta=h.future(1))])

    def test_3_recovery_to_django_without_either_app_api(self):
        self.recover_without_apis('go', 'django', lambda: [self.mail('go', eta=h.future(30))[0]])

    # -- installed mutation controls (Go mode) ----------------------------------------------------------------------
    def test_4_mutation_missing_worker(self):
        self.ensure('go')
        self.assert_clean([self.services('go')], 'control: all Go services')
        p5.run([*self.compose_files(), '--profile', 'go', 'stop', 'worker-go'], log=self.inst.log, env=self.compose_env())
        try:
            self.assert_refused([self.services('go')], 'missing_worker', 'worker-go stopped')
        finally:
            p5.run([*self.compose_files(), '--profile', 'go', 'start', 'worker-go'], log=self.inst.log,
                   env=self.compose_env())

    def test_5_mutation_unresolved_task(self):
        self.ensure('go')
        due = self.database_task('go')
        self.wait_completed([due])
        self.assert_clean([{'kind': 'work', 'runtime': 'go', 'items': self.work_items('go', [(due, False)])}],
                          'control: a completed task')
        unknown = self.inst.seed(h.QUEUE['go'], 'p7.release.task_nobody_runs')
        try:
            self.wait_completed([unknown], timeout=60)
            items = self.work_items('go', [(due, False), (unknown, False)])
            self.assert_refused([{'kind': 'work', 'runtime': 'go', 'items': items}], 'unresolved_work',
                                'a task nobody can run')
        finally:
            self.inst.psql(f"UPDATE public.assozeta_handoff_work SET state='cancelled', resolved_at=clock_timestamp(), "
                           f"resolved_by='p7-fixture' WHERE task_id='{unknown}';")
            for queue in h.QUEUE.values():
                for raw in self.inst.redis('LRANGE', queue, '0', '-1').splitlines():
                    if unknown.encode() in raw:
                        self.inst.redis('LREM', queue, '0', raw.decode())

    def test_6_mutation_denied_pdf_host(self):
        self.ensure('go')
        self.assert_clean([self.renders('go')], 'control: the renderer renders active-api')
        override = STATE['work'] / 'p7-renderer-old-host.yml'
        override.write_text('services:\n  renderer:\n    environment:\n'
                            '      RENDER_ALLOWED_HOSTS: api,minio,cdn.jsdelivr.net\n')
        p5.run([*self.compose_files(override), 'up', '-d', '--no-deps', 'renderer'], log=self.inst.log,
               env=self.compose_env())
        try:
            self.assert_refused([self.renders('go')], 'pdf_render_failed', 'renderer allowlist without active-api')
        finally:
            p5.run([*self.compose_files(), 'up', '-d', '--no-deps', 'renderer'], log=self.inst.log,
                   env=self.compose_env())

    def test_7_mutation_forced_old_runtime_http_call(self):
        self.ensure('go')
        since = time.time()
        self.assert_clean([self.inactive('go', since)], 'control: Django stays stopped in Go mode')
        p5.run([*self.compose_files(), '--profile', 'django', 'up', '-d', '--no-deps', 'api'], log=self.inst.log,
               env=self.compose_env())
        try:
            # Django serves its health route at /healthz on its own port (the proxy adds /api).
            call = ("fetch('http://api:8000/healthz').then(r => console.log(r.status))"
                    ".catch(e => console.log(String(e)))")
            try:
                h.wait_for(lambda: '200' in p5.run([*self.compose_files(), 'exec', '-T', 'renderer', 'node', '-e', call],
                                                   log=self.inst.log, check=False, env=self.compose_env()).stdout,
                           'the forced call to the Django API', timeout=300, interval=3)
            except AssertionError as error:
                self.fail(f'fixture: {error}')
            self.assert_refused([self.inactive('go', since)], 'inactive_runtime_call', 'the Django API called in Go mode')
        finally:
            p5.run([*self.compose_files(), '--profile', 'django', 'rm', '-s', '-f', 'api'], log=self.inst.log,
                   check=False, env=self.compose_env())
            self.inst.cli('restart')

    def test_8_mutation_duplicate_provider_effect(self):
        self.ensure('go')
        mail, subject = self.mail('go')
        self.wait_delivered([subject])
        self.assert_clean([self.effects('go', [subject])], 'control: one accepted message')
        self.sink.deliver_again(subject)
        self.assert_refused([self.effects('go', [subject])], 'duplicate_effect', 'the provider accepted it twice')


if __name__ == '__main__':
    unittest.main()
