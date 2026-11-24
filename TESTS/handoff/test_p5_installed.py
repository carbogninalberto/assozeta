"""Phase 5 part D: the installed dual-runtime lanes (CONTRACT P5-D v1).

A real installation of this commit: the five release images built from the checkout
(backend, backend-go, web, renderer, updater), the distribution's own CLI
(configure, provision of the updater runner, start), real Compose, PostgreSQL, Redis,
MinIO, renderer and Caddy, an owner configured through first-run setup. Every switch
is requested the way the owner does it (POST /api/instance/admin/runtime through the
public proxy, password-confirmed) or the way the operator does it
(./bin/assozeta runtime-switch|runtime-recover), and followed only through surfaces
that work with neither application API up (the runner status socket
POST /instance-update-status, key 'runtime').

Lanes (each test brings the installation to its starting mode itself, so one lane can
run alone; in order they run forward, reverse, a second round trip with rapid and
opposite requests, failed-before-commit and failed-after-commit):
  forward / reverse   accepted work seeded at the source (a database-only task due tomorrow and
      a mail task due next month, so neither completes before the switch) is accounted for on the target by identity; the
      owner's access token from before the switch still works afterwards (session
      preserved) and a settings write made on one runtime reads back on the other;
      `docker compose ps` runs exactly the shared services plus the target set; every
      running application container runs the target image; the container event history
      since the commit shows no start/create of an opposite-runtime container (no hidden
      restart, no opposite one-shot) and, before the commit, only a target one-shot
      (the private preflight); the notification socket reconnects (HTTP 101) on /ws/ and
      /api/v2/ws/ with the same token; the status passes through the maintenance window
      while public readiness is unavailable and ends with effective = intended = target.
  browser (forward)   a signed-in owner (TESTS/browser/p5-installed-runtime.mjs, real Chromium, unmodified
      UI, no interception) has the Runtime tab open before submission and a second route open; submits
      once with the password; while public readiness is unavailable a cold signed-in browser shows the
      same operation id (from the controller's status); the open tab reaches effective = intended =
      target; the other route reconnects its socket without a page reload.
  password            a wrong owner password answers exactly HTTP 400 {"error": "Password non corretta."}
      on the active API in either mode and starts no operation (also pinned on both APIs by the
      instance-runtime HTTP family).
  round trip + rapid  a request for the opposite target while one runs answers 409
      operation_in_progress with the running operation; replaying the first request's
      idempotency key after completion (served by the other runtime) returns the same
      operation; generations are monotonic.
  failed before commit  the target image cannot start (its reference points to an image
      that does not exist): the operation fails before the commit, the source keeps
      serving, no target container ever started, status offers recovery_action 'retry'.
  failed after commit   `./bin/assozeta runtime-switch go` runs with ASSOZETA_HANDOFF_BARRIER_DIR set to a
      directory inside the installation root (test-only seam: the CLI forwards that variable to the
      updater container only then; runtime_switch.py honours it, P5-A) holding 'activate:started';
      once the controller arrives there (authority committed, maintenance still on) its container is
      killed; `runtime-recover` finishes forward to a healthy target with the work accounted for.
This lane closes t-p0's RED2 intent (a cold client reaches Go after a Go request, Django
stopped) and the Phase 2 coverage gaps (#89, #90): a Go-mode installation switched
through the real controller with packaged Go writes read back by Django.

Images: built once per run (tags assozeta-p5-<name>:<revision>), or given by
ASSOZETA_HANDOFF_P5_IMAGES='{"backend": ..., "backend-go": ..., "web": ..., "renderer": ...,
"updater": ...}'. ASSOZETA_HANDOFF_P5_LANES=forward,reverse,... selects lanes.
Cost: ~20-25 min of image builds on a cold CI runner, then ~10-15 min of lanes; its own
CI job (handoff-contracts.yml 'installed'). Everything is uniquely named and removed
(`bin/assozeta uninstall --volumes --yes`, the runner project, the work directory).

Run: python3 -B -m unittest -v TESTS.handoff.test_p5_installed
"""
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from TESTS.handoff import p5_harness as h
from TESTS.handoff.topology import BUNDLE, ROOT

RED = 'P5 contract red'
DJANGO_APP, GO_APP = h.DJANGO_APP, h.GO_APP
LANES = [lane for lane in os.environ.get('ASSOZETA_HANDOFF_P5_LANES', '').split(',') if lane]
OWNER_EMAIL, OWNER_PASSWORD = 'owner-p5@example.invalid', 'P5-' + secrets.token_hex(12) + '!a'
STATE = {}


def redact(text):
    for secret in STATE.get('secrets', ()):
        text = text.replace(secret, '[redacted]')
    return text


def run(command, *, log, check=True, timeout=1800, **kwargs):
    result = subprocess.run([str(part) for part in command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout, **kwargs)
    text = f'$ {" ".join(map(str, command))[:400]}\n{result.stdout[-6000:]}\n'
    for secret in STATE.get('secrets', ()):
        text = text.replace(secret, '[redacted]')
    log.write(text)
    log.flush()
    if check and result.returncode:
        raise h.HarnessError(f'{Path(str(command[0])).name} {" ".join(map(str, command[1:3]))} failed: '
                             f'{result.stdout[-1500:]}')
    return result


def free_port():
    return h.free_port()


class Installed:
    def __init__(self, work, images, log):
        self.work, self.images, self.log = work, images, log
        self.root = work / 'install'
        self.root.mkdir(parents=True)
        for name in BUNDLE:
            source = ROOT / 'selfhost' / name
            if source.is_dir():
                shutil.copytree(source, self.root / name, ignore=shutil.ignore_patterns('__pycache__'))
            elif source.exists():
                shutil.copy2(source, self.root / name)
        self.env_file = self.root / '.env'
        self.tools = work / 'tools'
        self.tools.mkdir()
        if not shutil.which('flock'):
            # macOS hosts: the same portable advisory-lock stand-in as TESTS/handoff/topology.py.
            (self.tools / 'flock').write_text(f'#!/bin/sh\nexec "{sys.executable}" -c "import fcntl,sys; '
                                              f'fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX)" "$@"\n')
            (self.tools / 'flock').chmod(0o755)
        self.project = 'assozeta-p5-' + uuid.uuid4().hex[:8]
        self.port = free_port()
        self.base = f'http://localhost:{self.port}'

    # -- distribution CLI ---------------------------------------------------------------------------------------
    def cli(self, *arguments, check=True, timeout=1800):
        environment = {key: value for key, value in os.environ.items() if not key.startswith('ASSOZETA_')}
        environment.update(ASSOZETA_ENV_FILE=str(self.env_file), PATH=f'{self.tools}{os.pathsep}{os.environ["PATH"]}')
        return run([self.root / 'bin/assozeta', *arguments], log=self.log, check=check, timeout=timeout,
                   cwd=self.root, env=environment)

    def env(self):
        sys.path[:0] = [p for p in (str(h.UPDATER), str(ROOT / 'BE/instance')) if p not in sys.path]
        from common import read_env
        return read_env(self.env_file)

    def set_env(self, **values):
        lines, seen = self.env_file.read_text().splitlines(), set()
        for index, line in enumerate(lines):
            key = line.split('=', 1)[0]
            if key in values:
                lines[index], _ = f'{key}={values[key]}', seen.add(key)
        lines += [f'{key}={value}' for key, value in values.items() if key not in seen]
        self.env_file.write_text('\n'.join(lines) + '\n')

    def install(self):
        # A release candidate's images carry its version (/app/VERSION); the CLI checks it at start.
        self.cli('configure', '--domain', f'localhost:{self.port}', '--version',
                 os.environ.get('ASSOZETA_RELEASE_VERSION') or 'test')
        # The retained log never carries this disposable installation's credentials.
        STATE['secrets'] = sorted({value for key, value in self.env().items() if len(value) >= 16 and any(
            word in key for word in ('PASSWORD', 'SECRET', 'TOKEN', 'KEY'))} | {OWNER_PASSWORD}, key=len, reverse=True)
        self.set_env(COMPOSE_PROJECT_NAME=self.project, HTTP_PORT=self.port, HTTPS_PORT=free_port(),
                     ASSOZETA_BACKEND_REF=self.images['backend'], ASSOZETA_BACKEND_GO_REF=self.images['backend-go'],
                     ASSOZETA_WEB_REF=self.images['web'], ASSOZETA_RENDERER_REF=self.images['renderer'],
                     ASSOZETA_UPDATER_REF=self.images['updater'])
        run(['docker', 'run', '--rm', '-v', f'{self.root}:{self.root}', '-v', '/var/run/docker.sock:/var/run/docker.sock',
             self.images['updater'], '/runner/provision.py', self.root, self.env_file], log=self.log)
        self.cli('start')
        values = self.env()
        response = self.http('POST', '/api/instance/configure', headers={'X-Setup-Token': values['INSTANCE_SETUP_TOKEN']},
                              body={'domain': f'localhost:{self.port}',
                                    'oem': {'name': 'P5 Club', 'abbreviation': 'P5', 'primaryColor': '#351DC2',
                                            'supportEmail': OWNER_EMAIL, 'logo': ''}, 'oauth': {}, 'stripe': {},
                                    'initialization': {'type': 'fresh', 'associationName': 'P5 Association',
                                                       'ownerEmail': OWNER_EMAIL, 'ownerPassword': OWNER_PASSWORD}})
        if response[0] != 200:
            raise h.HarnessError(f'first-run setup failed: HTTP {response[0]}')
        status, login = self.http('POST', '/api/oauth2/login', body={'username': OWNER_EMAIL, 'password': OWNER_PASSWORD})
        if status != 200:
            raise h.HarnessError(f'owner login failed: HTTP {status}')
        self.token, self.refresh = login['access_token'], login.get('refresh_token')

    def collect(self):
        """Copy the updater's operation logs, .operations/ and the runner container log into
        quality-reports/handoff/diagnostics/<project>/ (the CI artifact), with this installation's
        generated credentials redacted."""
        target = DIAGNOSTICS / self.project
        target.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(['docker', 'run', '--rm', '-v', f'{self.root}:{self.root}', '--entrypoint', 'sh',
                                  self.images.get('updater') or 'alpine', '-c',
                                  'cd "$1" && tar -cf - $(ls -d .updater/logs .operations 2>/dev/null)', 'collect',
                                  str(self.root)], capture_output=True, timeout=300)
        if archive.returncode == 0 and archive.stdout:
            import io
            import tarfile
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as files:
                for member in files.getmembers():
                    if member.isfile() and member.size < 5_000_000:
                        content = files.extractfile(member).read().decode('utf-8', 'replace')
                        path = target / member.name.lstrip('./').replace('/', '__')
                        path.write_text(redact(content))
        runner = subprocess.run(['docker', 'logs', f'{self.project}-updater-updater-1'], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=120)
        (target / 'updater-container.log').write_text(redact(runner.stdout[-200000:]))

    def uninstall(self):
        if self.env_file.exists():
            try:
                self.collect()
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                self.log.write(f'diagnostics not collected: {error}\n')
            self.cli('uninstall', '--volumes', '--yes', check=False)
            run(['docker', 'compose', '--project-name', f'{self.project}-updater', '-f', self.root / 'compose.updater.yml',
                 '--env-file', self.env_file, 'down', '--volumes', '--remove-orphans'], log=self.log, check=False,
                env={**os.environ, 'ASSOZETA_SELFHOST_DIR': str(self.root), 'ASSOZETA_ENV_FILE': str(self.env_file),
                     'ASSOZETA_UPDATER_API_VOLUME': f'{self.project}_updater_api',
                     'ASSOZETA_UPDATER_STATUS_VOLUME': f'{self.project}_updater_status'})
            for suffix in ('updater_api', 'updater_status'):
                run(['docker', 'volume', 'rm', '-f', f'{self.project}_{suffix}'], log=self.log, check=False)

    # -- observation --------------------------------------------------------------------------------------------
    def compose(self, *arguments, check=True, stdin=None):
        from common import compose_command, compose_environment
        return run(compose_command(self.root, self.env_file) + list(arguments), log=self.log, check=check,
                   env=compose_environment(self.root, self.env_file), input=stdin)

    def psql(self, sql):
        values = self.env()
        result = run(['docker', 'compose', '--env-file', self.env_file, '-f', self.root / 'compose.yml', 'exec', '-T',
                      'postgres', 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', values['DBUSER'], '-d',
                      values['DBNAME']], log=self.log, input=sql,
                     env={**os.environ, 'ASSOZETA_ENV_FILE': str(self.env_file), 'ASSOZETA_SELFHOST_DIR': str(self.root)})
        return result.stdout.strip()

    def redis(self, *arguments, payload=None):
        values = self.env()
        command = ['docker', 'compose', '--env-file', self.env_file, '-f', self.root / 'compose.yml', 'exec', '-T',
                   '-e', f'REDISCLI_AUTH={values["REDIS_PASSWORD"]}', 'redis', 'redis-cli', '--no-auth-warning']
        if payload is not None:
            command.append('-x')
        result = subprocess.run([str(c) for c in command + list(arguments)], input=payload, capture_output=True,
                                timeout=60, env={**os.environ, 'ASSOZETA_ENV_FILE': str(self.env_file),
                                                 'ASSOZETA_SELFHOST_DIR': str(self.root)})
        if result.returncode:
            raise h.HarnessError('redis-cli failed')
        return result.stdout

    def authority(self):
        runtime, generation, operation, committed = self.psql(
            "SELECT runtime, generation, coalesce(operation_id::text, ''), "
            "coalesce(extract(epoch FROM committed_at)::text, '0') FROM public.assozeta_go_job_ownership "
            'WHERE singleton=1;').split('|')
        return runtime, int(generation), operation or None, float(committed)

    def running(self):
        output = self.compose('--profile', '*', 'ps', '--status', 'running', '--services').stdout
        return {line.strip() for line in output.splitlines() if line.strip()}

    def containers(self):
        output = run(['docker', 'ps', '--filter', f'label=com.docker.compose.project={self.project}',
                      '--format', '{{.ID}}'], log=self.log).stdout.split()
        if not output:
            return []
        documents = json.loads(run(['docker', 'inspect', *output], log=self.log).stdout)
        return [{'service': d['Config']['Labels'].get('com.docker.compose.service'), 'image': d['Config']['Image'],
                 'entrypoint': d['Config'].get('Entrypoint') or [], 'oneoff': d['Config']['Labels'].get(
                     'com.docker.compose.oneoff') == 'True'} for d in documents]

    def events(self, since, until=None):
        until = until or time.time()
        output = run(['docker', 'events', '--since', f'{since:.3f}', '--until', f'{until:.3f}', '--filter',
                      f'label=com.docker.compose.project={self.project}', '--filter', 'type=container',
                      '--format', '{{json .}}'], log=self.log, timeout=120).stdout
        found = []
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            attributes = event.get('Actor', {}).get('Attributes', {})
            found.append({'action': event.get('Action') or event.get('status'), 'time': event.get('timeNano', 0) / 1e9,
                          'service': attributes.get('com.docker.compose.service'), 'image': attributes.get('image'),
                          'oneoff': attributes.get('com.docker.compose.oneoff') == 'True'})
        return found

    def http(self, method, path, token=None, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, method=method)
        request.add_header('Content-Type', 'application/json')
        if token:
            request.add_header('Authorization', f'Bearer {token}')
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urlopen(request, timeout=60) as response:
                payload = response.read()
                return response.status, (json.loads(payload) if payload.strip().startswith(b'{') else None)
        except HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload)
            except ValueError:
                return error.code, None
        except (URLError, OSError):
            return None, None

    def runtime_status(self, token=None):
        status, payload = self.http('POST', '/instance-update-status', token=token or self.token, body={})
        return status, (payload or {}).get('runtime')

    def websocket(self, path, token=None):
        key = base64.b64encode(os.urandom(16)).decode()
        with socket.create_connection(('localhost', self.port), 20) as sock:
            sock.sendall((f'GET {path}?token={token or self.token} HTTP/1.1\r\nHost: localhost:{self.port}\r\n'
                          f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                          f'Sec-WebSocket-Version: 13\r\nOrigin: {self.base}\r\n\r\n').encode())
            line = sock.makefile('rb').readline().decode(errors='replace')
        return int(line.split()[1]) if line.startswith('HTTP/') else None

    # -- seeding ------------------------------------------------------------------------------------------------
    def seed(self, queue, task, eta=None, args=()):
        task_id = str(uuid.uuid4())
        runtime = self.authority()[0]
        payload = h.envelope(task, task_id, args=args, queue=queue, eta=eta)
        outcome = self.psql(f"SELECT outcome FROM public.assozeta_handoff_admit('{runtime}', '{task_id}', '{task}', "
                            f"'broker', '{queue}', decode('{base64.b64encode(payload).decode()}', 'base64'), "
                            f"clock_timestamp(), {h.repr_ts(eta)}, NULL);")
        if outcome != 'accepted':
            raise h.HarnessError(f'fixture admission answered {outcome}')
        self.redis('LPUSH', queue, payload=payload)
        return task_id

    def ledger(self, task_id):
        line = self.psql(f"SELECT state||'|'||owner_runtime||'|'||owner_generation FROM public.assozeta_handoff_work "
                         f"WHERE task_id='{task_id}';")
        return tuple(line.split('|')) if line else None

    def copies(self, task_id):
        found = {}
        for queue in ('celery', 'assozeta-go'):
            for raw in self.redis('LRANGE', queue, '0', '-1').splitlines():
                if task_id.encode() in raw:
                    found[queue] = found.get(queue, 0) + 1
        return found


def diagnosis(operation):
    """The failure fields of a runtime operation, for assertion messages (no payloads or credentials)."""
    return {key: operation.get(key) for key in ('status', 'stage', 'error_code', 'failed_stage', 'error',
                                                 'recovery_action', 'abort_code', 'abort_stage')}


def images(log):
    given = os.environ.get('ASSOZETA_HANDOFF_P5_IMAGES')
    if given:
        return json.loads(given)
    revision = run(['git', '-C', ROOT, 'rev-parse', '--short=12', 'HEAD'], log=log).stdout.strip()
    built, timings = {}, {}
    for name, command in (('backend', ['--target', 'production', '--build-arg', 'VERSION=test', 'BE']),
                          ('backend-go', ['--build-arg', 'VERSION=test', 'BE_GO']),
                          ('web', ['--target', 'production', 'UI']),
                          ('renderer', ['selfhost/renderer']),
                          ('updater', ['-f', 'selfhost/updater/Dockerfile', '.'])):
        tag = f'assozeta-p5-{name}:{revision}'
        started = time.monotonic()
        run(['docker', 'build', '-t', tag, *command], log=log, cwd=ROOT, timeout=3600)
        built[name], timings[name] = tag, round(time.monotonic() - started)
    STATE['build_seconds'] = timings
    return built


OUTPUT = ROOT / 'quality-reports/handoff/p5/installed'
DIAGNOSTICS = ROOT / 'quality-reports/handoff/diagnostics'


def setUpModule():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    STATE['log'] = log = (OUTPUT / 'lanes.log').open('w')
    # The installation root is bind-mounted into the updater container at the same path.
    work = Path(os.environ.get('ASSOZETA_HANDOFF_P5_WORKDIR') or OUTPUT)
    work.mkdir(parents=True, exist_ok=True)
    STATE['work'] = Path(tempfile.mkdtemp(prefix='run-', dir=work))
    try:
        built = images(log)
        installed = Installed(STATE['work'], built, log)
        STATE['installed'] = installed
        installed.install()
    except BaseException:
        tearDownModule()  # unittest skips tearDownModule when setUpModule fails
        raise


def tearDownModule():
    installed = STATE.get('installed')
    try:
        if installed:
            installed.uninstall()
    finally:
        if STATE.get('work'):
            shutil.rmtree(STATE['work'], ignore_errors=True)
        report = {key: value for key, value in STATE.items() if key in ('build_seconds', 'lanes')}
        (OUTPUT / 'report.json').write_text(json.dumps(report, indent=1))
        if STATE.get('log') and not STATE['log'].closed:
            STATE['log'].close()


class InstalledLanes(h.ContractCase, unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.inst = STATE['installed']
        lane = self._testMethodName.split('_', 2)[2]
        if LANES and not any(lane.startswith(item) for item in LANES):
            self.skipTest('lane not selected')  # local selection only; CI runs every lane

    # -- helpers ------------------------------------------------------------------------------------------------
    def request(self, target, key=None):
        generation = self.inst.authority()[1]
        key = key or str(uuid.uuid4())
        status, payload = self.inst.http('POST', '/api/instance/admin/runtime', self.inst.token,
                                         {'target': target, 'expected_generation': generation, 'idempotency_key': key,
                                          'password': OWNER_PASSWORD})
        self.assertEqual(status, 202, f'{RED}: the owner request for {target} was not accepted: {status} {payload}')
        return payload['operation']['id'], key

    def wrong_password(self, target):
        inst = self.inst
        before = (inst.runtime_status()[1] or {}).get('operation') or {}
        code, payload = inst.http('POST', '/api/instance/admin/runtime', inst.token,
                                  {'target': target, 'expected_generation': inst.authority()[1],
                                   'idempotency_key': str(uuid.uuid4()), 'password': 'P5-wrong-password'})
        self.assertEqual((code, payload), (400, {'error': 'Password non corretta.'}),
                         f'{RED}: a wrong owner password must answer exactly 400 Password non corretta.')
        after = (inst.runtime_status()[1] or {}).get('operation') or {}
        self.assertEqual(after.get('id'), before.get('id'), f'{RED}: a wrong password started an operation')

    def browser_switch(self, target):
        inst = self.inst
        claims = json.loads(base64.urlsafe_b64decode(inst.token.split('.')[1] + '=='))
        user = {'user_id': claims['user_id'], 'username': OWNER_EMAIL, 'first_name': 'P5', 'role': 'administrator',
                'is_superuser': True}
        session = {'sessionToken': inst.token, 'refreshToken': inst.refresh, 'expires': int(time.time() * 1000) + 3600000,
                   'role': 'administrator', 'userData': user, 'subPage': 'self-instance', 'permissions': [],
                   'snoozeTestimonial': True}
        report_path = STATE['work'] / f'browser-{target}.json'
        result = subprocess.run(['node', ROOT / 'TESTS/browser/p5-installed-runtime.mjs'], capture_output=True,
                                text=True, timeout=2400, env={**os.environ, 'P5_BASE_URL': inst.base,
                                'P5_SESSION': json.dumps(session), 'P5_PASSWORD': OWNER_PASSWORD, 'P5_TARGET': target,
                                'P5_REPORT': str(report_path), 'PLAYWRIGHT_MODULE': str(
                                    ROOT / 'selfhost/tests/browser/node_modules/playwright/index.mjs')})
        report = json.loads(report_path.read_text()) if report_path.exists() else {
            'failure': 'no report: ' + (result.stderr or result.stdout)[-600:]}
        STATE.setdefault('lanes', []).append({'browser': target, 'checks': report.get('checks')})
        failure = report.get('failure')
        self.assertIsNone(failure, f'{RED}: browser step: {failure}')
        return report['operation']

    def follow(self, operation_id, timeout=1800):
        """Poll the runner status socket (works with neither API up) until the operation ends."""
        seen, deadline, unanswered = [], time.monotonic() + timeout, time.monotonic()
        while time.monotonic() < deadline:
            code, status = self.inst.runtime_status()
            if code == 200 and status is None:
                self.fail(f'{RED}: POST /instance-update-status answers without the runtime status (key runtime)')
            if code == 200:
                unanswered = time.monotonic()
            elif time.monotonic() - unanswered > 120:
                self.fail(f'{RED}: the runner status socket stopped answering during the switch (HTTP {code})')
            ready, _ = self.inst.http('GET', '/api/readyz')
            operation = (status or {}).get('operation') or {}
            seen.append({'code': code, 'stage': operation.get('stage'), 'ready': ready,
                         'maintenance': (status or {}).get('maintenance')})
            if operation.get('id') == operation_id and operation.get('status') in ('succeeded', 'failed',
                                                                                    'recovery_required'):
                if operation.get('status') != 'succeeded':
                    self.inst.log.write(f'operation {operation_id} ended: {json.dumps(diagnosis(operation))}\n')
                return status, seen
            time.sleep(1)
        self.fail(f'{RED}: operation {operation_id} did not finish within {timeout} s; last {seen[-3:]}')

    def ensure(self, runtime):
        if self.inst.authority()[0] != runtime:
            operation, _ = self.request(runtime)
            status, _ = self.follow(operation)
            self.assertEqual(status['operation']['status'], 'succeeded', f'{RED}: could not reach {runtime} mode: '
                                                                         f'{diagnosis(status["operation"])}')

    def assert_topology(self, runtime, since_commit):
        inst = self.inst
        target, other = (GO_APP, DJANGO_APP) if runtime == 'go' else (DJANGO_APP, GO_APP)
        image = inst.images['backend-go' if runtime == 'go' else 'backend']
        self.assertEqual(sorted(inst.running() - h.SHARED), sorted(target),
                         f'{RED}: running application services after the switch to {runtime}')
        for container in inst.containers():
            if container['service'] in target:
                self.assertEqual(container['image'], image, f'{RED}: {container["service"]} runs another image')
        late = [e for e in inst.events(since_commit) if e['action'] in ('create', 'start', 'restart')
                and (e['service'] in other or e['image'] == inst.images['backend-go' if runtime == 'django' else 'backend'])]
        self.assertEqual(late, [], f'{RED}: an opposite-runtime container started after the commit (hidden restart '
                                   'or one-shot)')

    def assert_switched(self, runtime, generation, operation_id, seen, work):
        inst = self.inst
        status = inst.runtime_status()[1] or {}
        operation = status.get('operation') or {}
        self.assertEqual((status.get('effective_runtime'), status.get('intended_runtime'), status.get('generation'),
                          operation.get('id'), operation.get('status'), status.get('maintenance')),
                         (runtime, runtime, generation, operation_id, 'succeeded', False),
                         f'{RED}: final status of the switch to {runtime}')
        self.assertTrue(any(entry['ready'] != 200 and entry['code'] == 200 for entry in seen),
                        f'{RED}: no moment with the API down while the status kept being served (maintenance window)')
        authority = inst.authority()
        self.assertEqual(authority[:3], (runtime, generation, operation_id), f'{RED}: authority after the switch')
        self.assert_topology(runtime, authority[3])
        code, view = inst.http('GET', '/api/instance/admin/runtime', inst.token)
        self.assertEqual((code, (view or {}).get('effective_runtime')), (200, runtime),
                         f'{RED}: the session from before the switch must keep working')
        for path in ('/ws/notifications/', '/api/v2/ws/notifications/'):
            self.assertEqual(inst.websocket(path), 101, f'{RED}: socket {path} does not reconnect after the switch')
        for label, task_id in work.items():
            row = inst.ledger(task_id)
            self.assertTrue(row and row[1] == runtime and int(row[2]) == generation and row[0] in
                            ('pending', 'running', 'completed'), f'{RED}: {label} {task_id} not accounted on {runtime}: {row}')
            stale = inst.copies(task_id).get(h.QUEUE['go' if runtime == 'django' else 'django'])
            self.assertFalse(stale, f'{RED}: {label} left on the inactive queue')
        STATE.setdefault('lanes', []).append({'runtime': runtime, 'generation': generation,
                                              'stages': sorted({e['stage'] for e in seen if e['stage']})})

    # -- lanes --------------------------------------------------------------------------------------------------
    def test_1_forward(self):
        inst = self.inst
        self.ensure('django')
        generation = inst.authority()[1]
        # Not due before the switch: due work would complete on the source runtime first and stay its own.
        work = {'tomorrow': inst.seed('celery', h.DB_TASK, eta=h.future(1)), 'next month': inst.seed('celery', h.MAIL_TASK, eta=h.future(30),
                                                                                    args=h.mail_args(1))}
        self.assertEqual(inst.websocket('/ws/notifications/'), 101, 'the socket works before the switch (control)')
        self.wrong_password('go')
        operation = self.browser_switch('go')
        status, seen = self.follow(operation)
        self.assert_switched('go', generation + 1, operation, seen, work)
        tables = {'p5': {'columns': ['name'], 'written_by': 'go'}}
        written, _ = inst.http('POST', '/api/profile/settings/tables', inst.token, {'tables_settings': tables})
        self.assertIn(written, (200, 201, 204), f'{RED}: a settings write through the Go API failed: {written}')
        STATE['tables'] = tables

    def test_2_reverse(self):
        inst = self.inst
        self.ensure('go')
        generation = inst.authority()[1]
        work = {'tomorrow': inst.seed('assozeta-go', h.MAIL_TASK, eta=h.future(1), args=h.mail_args(2)),
                'next month': inst.seed('assozeta-go', h.MAIL_TASK, eta=h.future(30), args=h.mail_args(3))}
        self.wrong_password('django')
        operation, key = self.request('django')
        status, seen = self.follow(operation)
        self.assert_switched('django', generation + 1, operation, seen, work)
        code, replay = inst.http('POST', '/api/instance/admin/runtime', inst.token,
                                 {'target': 'django', 'expected_generation': generation, 'idempotency_key': key,
                                  'password': OWNER_PASSWORD})
        self.assertEqual((code, (replay or {}).get('replayed'), ((replay or {}).get('operation') or {}).get('id')),
                         (202, True, operation), f'{RED}: replaying the request on the new runtime')
        if STATE.get('tables'):
            code, settings = inst.http('GET', '/api/profile/settings', inst.token)
            # GET /api/profile/settings answers {'settings': {..., 'table_settings': ...}} on both runtimes.
            stored = (((settings or {}).get('settings') or {}).get('table_settings') or {}).get('p5')
            self.assertEqual((code, stored), (200, STATE['tables']['p5']),
                             f'{RED}: a write made on Go does not read back on Django')

    def test_3_round_trip_with_rapid_and_opposite_requests(self):
        inst = self.inst
        self.ensure('django')
        generation = inst.authority()[1]
        operation, _ = self.request('go')
        code, payload = inst.http('POST', '/api/instance/admin/runtime', inst.token,
                                  {'target': 'django', 'expected_generation': generation,
                                   'idempotency_key': str(uuid.uuid4()), 'password': OWNER_PASSWORD})
        self.assertIn(code, (409, 503), f'{RED}: an opposite request during a running switch: {code} {payload}')
        if code == 409:
            self.assertEqual((payload['code'], payload['active']['id']), ('operation_in_progress', operation),
                             f'{RED}: the opposite request must name the running operation')
        status, seen = self.follow(operation)
        self.assert_switched('go', generation + 1, operation, seen, {})
        back, _ = self.request('django')
        status, seen = self.follow(back)
        self.assert_switched('django', generation + 2, back, seen, {})

    def test_4_failed_before_commit(self):
        inst = self.inst
        self.ensure('django')
        before = inst.authority()
        reference = inst.env()['ASSOZETA_BACKEND_GO_REF']
        inst.set_env(ASSOZETA_BACKEND_GO_REF='assozeta-p5-missing/backend-go:absent')
        try:
            started = time.time()
            operation, _ = self.request('go')
            status, _ = self.follow(operation)
        finally:
            inst.set_env(ASSOZETA_BACKEND_GO_REF=reference)
        view = status['operation']
        self.assertEqual((view['status'], view['committed_generation'], view.get('recovery_action')),
                         ('failed', None, 'retry'), f'{RED}: a target that cannot start must fail before the commit')
        self.assertEqual(inst.authority()[:2], before[:2], f'{RED}: failed-before-commit changed the authority')
        self.assertEqual(sorted(inst.running() - h.SHARED), sorted(DJANGO_APP), f'{RED}: the source must keep serving')
        started_go = [e for e in inst.events(started) if e['action'] == 'start' and e['service'] in GO_APP
                      and not e['oneoff']]
        self.assertEqual(started_go, [], f'{RED}: a Go service started for a switch that never committed')
        self.assertEqual(inst.http('GET', '/api/readyz')[0], 200, f'{RED}: Django not serving after the failed switch')

    def test_5_failed_after_commit(self):
        inst = self.inst
        self.ensure('django')
        generation = inst.authority()[1]
        work = {'tomorrow': inst.seed('celery', h.DB_TASK, eta=h.future(1))}
        barriers = inst.root / '.p5-barriers'
        barriers.mkdir(exist_ok=True)
        (barriers / 'hold.activate:started.all').write_text('')
        environment = {key: value for key, value in os.environ.items() if not key.startswith('ASSOZETA_')}
        environment.update(ASSOZETA_ENV_FILE=str(inst.env_file), PATH=f'{inst.tools}{os.pathsep}{os.environ["PATH"]}',
                           ASSOZETA_HANDOFF_BARRIER_DIR=str(barriers))
        process = subprocess.Popen([str(inst.root / 'bin/assozeta'), 'runtime-switch', 'go'], cwd=inst.root,
                                   env=environment, stdout=inst.log, stderr=subprocess.STDOUT)
        try:
            h.wait_for(lambda: list(barriers.glob('arrived.activate:started.*')) or process.poll() is not None,
                       'the controller to reach activate:started', timeout=1800, interval=0.5)
            self.assertTrue(list(barriers.glob('arrived.activate:started.*')),
                            f'{RED}: the operator switch never reached activate:started (barrier seam)')
            self.assertEqual((inst.authority()[1], (inst.root / h.FLAG).exists()), (generation + 1, True),
                             f'{RED}: at activate:started the authority is committed and the proxy still closed')
            helpers = run(['docker', 'ps', '-q', '--filter', f'label=org.assozeta.lifecycle.installation={inst.root}'],
                          log=inst.log).stdout.split()
            self.assertTrue(helpers, f'{RED}: no controller container alive at activate:started')
            run(['docker', 'kill', *helpers], log=inst.log, check=False)
        finally:
            (barriers / 'hold.activate:started.all').unlink(missing_ok=True)
            process.wait(600)
        result = inst.cli('runtime-recover', check=False)
        self.assertEqual(result.returncode, 0, f'{RED}: runtime-recover after a post-commit kill: {result.stdout[-600:]}')
        status = inst.runtime_status()[1]
        self.assertEqual((status['effective_runtime'], status['operation']['status'], status['maintenance']),
                         ('go', 'succeeded', False), f'{RED}: recovery after the post-commit kill')
        self.assert_topology('go', inst.authority()[3])
        row = inst.ledger(work['tomorrow'])
        self.assertTrue(row and row[1] == 'go', f'{RED}: work accepted before the kill is not on Go: {row}')
        self.ensure('django')


if __name__ == '__main__':
    unittest.main()
