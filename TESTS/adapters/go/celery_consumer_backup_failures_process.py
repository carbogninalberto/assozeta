"""Differential harness for the Go Celery consumer: instance.backups.notify_failures.

Extends ``celery_consumer_process.py`` (private PostgreSQL with the current
Django schema, private Redis, recording fake SMTP server, the production
prefork Django worker as the oracle) with:

- a stub of the update runner: an HTTP server on ``<directory>/runner.sock``
  with the bearer token in ``<directory>/token`` (ASSOZETA_UPDATER_DIRECTORY),
  answering ``GET /status`` and ``GET /backups`` per scenario and recording
  every request (method, path, headers);
- the production cache (django-redis, database 1 of the private Redis) on the
  Django side, which the Go consumer shares.

Each scenario publishes the message as Beat does for the ``notify-backup-failures``
entry (``apply_async((), {}, headers={'periodic_task_name': ...})``) with
GO_TASK_QUEUE_TASKS=instance.backups.notify_failures, so the publish itself
checks the route to ``assozeta-go``. The identical bytes run on the Django
prefork worker and, after re-seeding the database, the cache and the runner
stub, on the race-built Go consumer. Compared: the SMTP transcripts, the
complete managed-model state with TaskResult rows, every broker key, every
cache key (value bytes and a TTL bucket) and the runner requests. Runner
timestamps are generated once per scenario relative to the clock, far from the
48-hour window edge. Handoff scenarios require Go to move the message
unchanged to the ``celery`` queue after only the runner reads; a Django worker
on that queue must then reach the Django-only final state.
"""
import hashlib
import http.server
import json
import os
import pickle
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go import celery_consumer_process as base
from TESTS.adapters.go.celery_consumer_process import (U, ASSOCIATION, OWNER, BRAND, HOST_USER, GO_QUEUE, DJANGO_QUEUE,
                                                       REDIS_PASSWORD, DATABASE)
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.email_tasks_worker_process import FakeSMTP, tls_context, free_port

OUTPUT = ROOT / 'quality-reports/go-migration/celery-consumer-backup-failures-process'
base.OUTPUT = OUTPUT  # consumer logs and difference.json of the shared Harness
DJANGO_ONLY = base.DJANGO_ONLY
TASK = 'instance.backups.notify_failures'
BEAT_NAME = 'notify-backup-failures'
RUNNER_TOKEN = 'fixture-runner-token'
NOTIFIED = ':1:instance:backup-failure-notified:'
WEEK = 7 * 86400
DOMAIN = 'istanza.example.test'


def iso(moment):
    return moment.isoformat()  # what the runner writes: datetime.now(timezone.utc).isoformat()


def failures(now):
    """A runner status and inventory with every kind of entry _failures reads."""
    status = {'history': [
        {'id': 'op-recent', 'kind': 'backup', 'status': 'failed', 'created_at': iso(now - timedelta(hours=3)),
         'updated_at': iso(now - timedelta(hours=2)), 'error': 'Spazio su disco esaurito'},
        {'id': 'op-restore', 'kind': 'restore', 'status': 'failed', 'updated_at': iso(now - timedelta(hours=1))},
        {'id': 'op-old', 'kind': 'backup', 'status': 'failed', 'updated_at': iso(now - timedelta(hours=60)), 'error': 'vecchio'},
        {'id': 'op-ok', 'kind': 'backup', 'status': 'succeeded', 'updated_at': iso(now - timedelta(hours=1))},
        {'id': 'op-created', 'kind': 'backup', 'status': 'failed', 'created_at': (now - timedelta(hours=5)).replace(microsecond=0)
         .isoformat().replace('+00:00', 'Z'), 'updated_at': None, 'error': ''},
        {'id': 'op-undated', 'kind': 'backup', 'status': 'failed'},
    ]}
    inventory = {'schedule': {'last_run': {'at': iso(now - timedelta(hours=1)), 'status': 'failed',
                                           'error': 'Backup automatico non eseguito: operazione in corso'}},
                 'remote': {'last_failure': {'at': iso(now - timedelta(minutes=30)), 'name': 'assozeta-20261109T100000Z.tar.gz',
                                             'error': 'Credenziali rifiutate'}}}
    return status, inventory


def quiet(now):
    status = {'history': [{'id': 'op-ok', 'kind': 'backup', 'status': 'succeeded', 'updated_at': iso(now)}]}
    inventory = {'schedule': {'last_run': {'at': iso(now - timedelta(hours=1)), 'status': 'failed', 'operation': 'op-ok',
                                           'error': 'riportato dall’operazione'}},
                 'remote': {'last_failure': {'at': iso(now - timedelta(hours=72)), 'error': 'scaduto'}}}
    return status, inventory


def one_failure(now):
    return {'history': [{'id': 'op-one', 'kind': 'backup', 'status': 'failed', 'updated_at': iso(now - timedelta(minutes=10))}]}, {}


def naive_after_one(now):
    status = {'history': [
        {'id': 'op-first', 'kind': 'backup', 'status': 'failed', 'updated_at': iso(now - timedelta(hours=1)), 'error': 'primo'},
        {'id': 'op-naive', 'kind': 'backup', 'status': 'failed', 'updated_at': (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()},
    ]}
    return status, {}


def body(value):
    return 200, json.dumps(value).encode()


class Scenario(base.Scenario):
    def __init__(self, name, *, runner=None, data=None, mode='production', instance='config', cached=(), smtp='ok', setup=None,
                 handoff=False, expect=None):
        super().__init__(name, {}, smtp=smtp, setup=setup, handoff=handoff, expect=expect)
        self.runner, self.data, self.mode, self.instance, self.cached = runner or {}, data, mode, instance, tuple(cached)


GETS = ['GET /status', 'GET /backups']
SCENARIOS = [
    Scenario('Beat message: failed backups, a scheduled run and the remote copy are each notified once', data=failures,
             expect={'status': 'SUCCESS', 'result': 4, 'mails': 4, 'requests': GETS,
                     'keys': ['op-created', 'op-recent', 'remote:<at>', 'schedule:<at>']}),
    Scenario('failures already notified are skipped', data=failures, cached=('op-recent', 'remote:'),
             expect={'status': 'SUCCESS', 'result': 2, 'mails': 2, 'requests': GETS,
                     'keys': ['op-created', 'op-recent', 'remote:<at>', 'schedule:<at>']}),
    Scenario('a scheduled run with its own operation and an old remote failure send nothing', data=quiet,
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': GETS, 'keys': []}),
    Scenario('an instance without a domain sends the notice without the link', data=one_failure, instance='no-domain',
             expect={'status': 'SUCCESS', 'result': 1, 'mails': 1, 'requests': GETS, 'keys': ['op-one']}),
    Scenario('development mode returns 0 without contacting the runner', data=failures, mode='development',
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': [], 'keys': []}),
    Scenario('an unreachable runner returns 0', data=failures, runner={'socket': False},
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': [], 'keys': []}),
    Scenario('a missing runner token returns 0', data=failures, runner={'token': ''},
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': [], 'keys': []}),
    Scenario('a runner refusal of the status returns 0', data=failures,
             runner={'/status': (409, json.dumps({'error': 'Operazione in corso'}).encode())},
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': ['GET /status'], 'keys': []}),
    Scenario('an inventory that is not JSON returns 0', data=failures, runner={'/backups': (200, b'<html>gateway</html>')},
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': GETS, 'keys': []}),
    Scenario('no instance configuration returns 0 after the runner reads', data=failures, instance=None,
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': GETS, 'keys': []}),
    Scenario('an instance without a primary association returns 0', data=failures, instance='no-primary',
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': GETS, 'keys': []}),
    Scenario('an owner without an e-mail address returns 0', data=failures, instance='owner-no-email',
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': GETS, 'keys': []}),
    Scenario('an SMTP login failure releases every notice for the next run', data=failures, smtp='auth',
             expect={'status': 'SUCCESS', 'result': 0, 'mails': 0, 'requests': GETS, 'keys': []}),
    Scenario('a restore completed after the publish refuses the run', data=failures, setup='restore-completed',
             expect={'status': 'FAILURE', 'exc': 'StaleDataTask', 'mails': 0, 'requests': [], 'keys': []}),
    Scenario('a timestamp without offset after a notified failure is handed off', data=naive_after_one, handoff=True,
             expect={'status': 'FAILURE', 'exc': 'TypeError', 'mails': 1, 'requests': GETS, 'keys': ['op-first']}),
]


# -- update runner stub ---------------------------------------------------------------------------
class RunnerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Runner:
    def __init__(self, directory):
        self.directory = directory
        self.requests, self.responses, self.server, self.thread = [], {}, None, None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                outer.requests.append({'method': 'GET', 'path': self.path,
                                       'headers': sorted([k.lower(), v] for k, v in self.headers.items())})
                code, data = outer.responses.get(self.path, (404, b'{"error": "sconosciuto"}'))
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def address_string(self):
                return 'runner'

            def log_message(self, *args):
                pass
        self.handler = Handler

    def configure(self, scenario, status, inventory):
        """The socket, the token and the two answers for one run."""
        self.stop()
        self.directory.mkdir(exist_ok=True)
        for name in ('runner.sock', 'token'):
            path = self.directory / name
            if path.exists() or path.is_socket():
                path.unlink()
        token = scenario.runner.get('token', RUNNER_TOKEN)
        (self.directory / 'token').write_text(token + '\n')
        self.responses = {'/status': body(status), '/backups': body(inventory)}
        self.responses.update({k: v for k, v in scenario.runner.items() if k.startswith('/')})
        self.requests = []
        if scenario.runner.get('socket', True):
            self.server = RunnerServer(str(self.directory / 'runner.sock'), self.handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()

    def take(self):
        requests, self.requests = self.requests, []
        return requests

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


class Stack(base.Stack):
    def __init__(self, work, environment, redis_port, smtp, runner):
        super().__init__(work, environment, redis_port, smtp)
        import redis
        self.runner = runner
        self.cache = redis.Redis(host='127.0.0.1', port=redis_port, db=1, password=REDIS_PASSWORD, socket_timeout=10)
        self.data = None

    def seed(self, scenario, stamp=None):
        result = super().seed(scenario, stamp)
        from application.models import User
        from instance.models import InstanceConfiguration
        started = self.seeding[0]
        if scenario.instance:
            if scenario.instance == 'owner-no-email':
                User.original_objects.filter(user_id=OWNER).update(email='')
            InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(
                domain='' if scenario.instance == 'no-domain' else DOMAIN, name='Istanza', self_hosted=True,
                primary_association_id=None if scenario.instance == 'no-primary' else ASSOCIATION)])
        status, inventory = self.data
        self.runner.configure(scenario, status, inventory)
        self.cache.flushdb()
        keys = self.keys(status, inventory)
        for prefix in scenario.cached:
            matches = [key for key in keys if key == prefix or (prefix.endswith(':') and key.startswith(prefix))]
            assert len(matches) == 1, f'cached key {prefix} must name one failure'
            # Written an hour ago by an earlier run (cache.add: pickle protocol 4 of True, 7 days).
            self.cache.set(NOTIFIED + matches[0], pickle.dumps(True, 4), ex=WEEK - 3600)
        self.seeding = (started, datetime.now(timezone.utc))
        return result

    @staticmethod
    def keys(status, inventory):
        keys = [item['id'] for item in status.get('history', []) if 'id' in item]
        for section, field in (('schedule', 'last_run'), ('remote', 'last_failure')):
            at = ((inventory.get(section) or {}).get(field) or {}).get('at')
            if at:
                keys.append(f'{section}:{at}')
        return keys

    def publish(self, scenario):
        """What Beat publishes for the notify-backup-failures entry (ModelEntry options: headers with the name)."""
        from instance.tasks import notify_backup_failures
        self.broker.flushdb()
        notify_backup_failures.apply_async((), {}, headers={'periodic_task_name': BEAT_NAME})
        raw = self.broker.rpop(GO_QUEUE)
        assert raw is not None and self.broker.llen(GO_QUEUE) == 0 and self.broker.llen(DJANGO_QUEUE) == 0, 'routed publish'
        message = json.loads(raw)
        assert message['headers']['task'] == TASK and message['headers']['periodic_task_name'] == BEAT_NAME, message['headers']
        return raw.decode()

    def cache_state(self, status, inventory):
        """Every cache key: the value bytes and the TTL as 'week' (a fresh cache.add) or 'seeded'."""
        names = {NOTIFIED + key: '<notified>:' + self.label(key) for key in self.keys(status, inventory)}
        state = {}
        for key in sorted(self.cache.keys('*')):
            name = key.decode()
            ttl = self.cache.ttl(key)
            bucket = 'week' if WEEK - 120 <= ttl <= WEEK else 'seeded' if WEEK - 3720 <= ttl <= WEEK - 3600 else ttl
            state[names.get(name, name)] = [self.cache.type(key).decode(), self.cache.get(key).hex(), bucket]
        return state

    @staticmethod
    def label(key):
        return key.split(':', 1)[0] + ':<at>' if key.startswith(('schedule:', 'remote:')) else key


class Harness(base.Harness):
    def oracle_settings(self, scenario):
        config = super().oracle_settings(scenario)
        config['routes'] = TASK
        config['settings'].update(ASSOZETA_DEPLOYMENT_MODE=scenario.mode, ASSOZETA_UPDATER_DIRECTORY=str(self.stack.runner.directory))
        return config

    def env_file(self, scenario):
        path = super().env_file(scenario)
        lines = [line for line in path.read_text().splitlines() if not line.startswith('GO_TASK_QUEUE_TASKS=')]
        lines += [f'GO_TASK_QUEUE_TASKS={TASK}', f'ASSOZETA_DEPLOYMENT_MODE={scenario.mode}',
                  f'ASSOZETA_UPDATER_DIRECTORY={self.stack.runner.directory}']
        path.write_text(''.join(line + '\n' for line in lines))
        return path

    def run_django(self, scenario, payload, queues=(GO_QUEUE,), push=True, wait=None):
        """The prefork oracle with the production cache (django-redis, database 1) on the private Redis."""
        if base.ORACLE != 'prefork':
            raise RuntimeError('this harness compares against the prefork oracle only')
        task_id = json.loads(payload)['headers']['id']
        if push:
            self.stack.push(scenario, payload)
        config = self.oracle_settings(scenario)
        assert config['routes'] and config['settings'], 'oracle settings'
        log = OUTPUT / f'django-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        environment = dict(os.environ, DJANGO_SETTINGS_MODULE='TESTS.adapters.go.consumer_prefork_oracle_settings',
                           CONSUMER_RESTORE_BROKER_URL=f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.stack.redis_port}',
                           CONSUMER_PREFORK_ORACLE=json.dumps(config), PYTHONPATH=f'{ROOT / "BE"}:{ROOT}',
                           OBJC_DISABLE_INITIALIZE_FORK_SAFETY='YES', CONSUMER_RESTORE_PRODUCTION_SERVICES='1',
                           CONSUMER_RESTORE_REDIS_PORT=str(self.stack.redis_port), CONSUMER_RESTORE_REDIS_PASSWORD=REDIS_PASSWORD,
                           AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture', AWS_STORAGE_BUCKET_NAME='fixture',
                           AWS_S3_ENDPOINT_URL='http://127.0.0.1:9', AWS_LOCATION='private')
        process = subprocess.Popen([sys.executable, '-m', 'TESTS.adapters.go.consumer_restore_worker', '-P', 'prefork', '-c', '1',
                                    '-Q', ','.join(queues), '-n', 'oracle@%h', '--without-heartbeat', '--without-gossip',
                                    '--without-mingle', '-l', 'WARNING'], cwd=ROOT / 'BE', env=environment, stdout=stream,
                                   stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            predicate = wait or (lambda: self.stack.quiescent(scenario, task_id))
            self.wait(lambda: predicate() or process.poll() is not None, 'Django worker: ' + scenario.name)
            assert process.poll() is None, 'Django worker exited: ' + log.read_text()[-3000:]
            time.sleep(0.3)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            try:
                code = process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                code = process.wait(timeout=10)
            stream.close()
        assert code == 0, f'Django worker exit status {code}: {log.read_text()[-3000:]}'
        time.sleep(0.5)  # the SMTP server records a connection when the worker's socket closes
        return task_id

    def run_go(self, scenario, payload, push=True, handoff=None):
        task_id = super().run_go(scenario, payload, push=push, handoff=handoff)
        if handoff if handoff is not None else scenario.handoff:
            # The plan reads the runner before it can refuse the inputs: only those two reads, nothing else.
            reads = [f"{r['method']} {r['path']}" for r in self.stack.runner.take()]
            assert reads == GETS, reads
            assert self.stack.cache.dbsize() == len(scenario.cached), 'handoff wrote the cache'
        return task_id

    @staticmethod
    def wait(predicate, description, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError('Timed out: ' + description)

    def observe(self, scenario, payload, since, until):
        observed = super().observe(scenario, payload, since, until)
        status, inventory = self.stack.data
        observed['cache'] = self.stack.cache_state(status, inventory)
        observed['runner'] = self.stack.runner.take()
        return observed

    def execute(self, scenario):
        self.stack.data = scenario.data(datetime.now(timezone.utc))
        try:
            super().execute(scenario)
        finally:
            self.stack.data = None

    def authored(self, scenario, payload, observed):
        """The Django oracle took the path the scenario was written for."""
        expect = scenario.expect
        own_id = json.loads(payload)['headers']['id']
        results = observed['task_results']
        assert len(results) == 1 and results[0]['task_id'] == own_id and results[0]['status'] == expect['status'], results
        assert results[0]['meta'] == '{"children": []}', results
        if 'exc' in expect:
            assert json.loads(results[0]['result'])['exc_type'] == expect['exc'], results
        else:
            assert json.loads(results[0]['result']) == expect['result'], results
        mails = [t for t in observed['smtp'] if '\\r\\n.\\r\\n' in t]
        assert len(mails) == expect['mails'], observed['smtp']
        assert [f"{r['method']} {r['path']}" for r in observed['runner']] == expect['requests'], observed['runner']
        for request in observed['runner']:
            assert ['authorization', f'Bearer {RUNNER_TOKEN}'] in request['headers'], request
        notified = sorted(name.split(':', 1)[1] for name, (_, _, bucket) in observed['cache'].items()
                          if name.startswith('<notified>:'))
        assert notified == expect['keys'], observed['cache']
        assert all(name.startswith('<notified>:') for name in observed['cache']), observed['cache']
        if expect['mails']:
            text = ''.join(mails)
            assert 'Backup non riuscito' in text or 'Copia remota non riuscita' in text, text
            assert (f'https://{DOMAIN}/#/profile?page=self-instance&tab=backups' in text) == (scenario.instance != 'no-domain'), text


def main():
    require_production_python()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = OUTPUT / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    redis_server = shutil.which('redis-server')
    if not Path(postgres).is_file() or not shutil.which('go') or not redis_server:
        raise RuntimeError('Private PostgreSQL, Redis and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    only = os.environ.get('CELERY_CONSUMER_ONLY')
    harness = None
    with tempfile.TemporaryDirectory(prefix='az-backup-notice-', dir='/tmp') as directory, (OUTPUT / 'services.log').open('w') as log:
        work = Path(directory); attempted = False; redis_process = None; smtp = None; runner = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            if not DJANGO_ONLY:
                subprocess.run(['go', 'build', '-race', '-o', str(work / 'celery-consumer'), './cmd/celery-consumer'], cwd=ROOT / 'BE_GO',
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass', REDIS_PASSWORD,
                                              '--save', '', '--appendonly', 'no', '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
            smtp = FakeSMTP(tls_context(work), False)
            runner = Runner(work / 'updater')
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            stack = Stack(work, environment, redis_port, smtp, runner)
            harness = Harness(stack, work / 'celery-consumer')
            for scenario in SCENARIOS:
                if only and only not in scenario.name:
                    continue
                print('START: ' + scenario.name, flush=True)
                harness.execute(scenario)
            if (work / 'celery-consumer').exists():
                result['binary_sha256'] = hashlib.sha256((work / 'celery-consumer').read_bytes()).hexdigest()
        finally:
            try:
                if harness:
                    result.update(scenarios=harness.completed, scenario_count=len(harness.completed))
                    (OUTPUT / 'observations.json').write_text(json.dumps(harness.observations, indent=1, default=str,
                                                                         ensure_ascii=False) + '\n')
                    for process, stream, _ in harness.stack.children:
                        if process.poll() is None:
                            process.kill()
                        stream.close()
            finally:
                try:
                    if runner:
                        runner.stop()
                    if smtp:
                        smtp.close()
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    if redis_process:
                        redis_process.terminate(); redis_process.wait(timeout=10)
                    if attempted:
                        try:
                            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                        except subprocess.CalledProcessError:
                            if (work / 'postgres/postmaster.pid').exists():
                                raise
                    report.write_text(json.dumps(result, indent=2) + '\n')
    expected = [s.name for s in SCENARIOS if not only or only in s.name]
    assert expected and harness.completed == expected, 'not every scenario completed'
    paths = ['TESTS/adapters/go/celery_consumer_backup_failures_process.py', 'TESTS/adapters/go/celery_consumer_process.py',
             *base.ORACLE_SOURCES, 'BE/instance/tasks.py', 'BE/instance/backups.py', 'BE/instance/updater_client.py',
             'BE/instance/email_configuration.py', 'BE/instance/restore/task_base.py', 'BE/core/task_routing.py', 'BE/core/settings.py']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'internal/celeryworker/', 'cmd/celery-consumer/',
                                                       'consumer_backup_failures', 'send_mail_async', 'internal/smtpsend/',
                                                       'internal/updaterrunner/')))
    result.update(success=True, partial=bool(only), race_instrumented=not DJANGO_ONLY, oracle=base.ORACLE, django_only=DJANGO_ONLY,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_hashes', 'scope')}))


if __name__ == '__main__':
    main()
