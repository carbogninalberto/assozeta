"""Differential harness for the Go consumer's prefork capabilities: acks_late, reject_on_worker_lost and
soft/hard time limits, as Celery 5.6's prefork worker (billiard AsynPool) applies them.

Private PostgreSQL (actual Django current schema) and a private Redis broker. The probe tasks of
``consumer_restore_probe_tasks.py`` (acks_late with reject_on_worker_lost, acks_late alone, early ack; task
defaults soft 1 s / hard 3 s) are published with Django's own producer, then each scenario runs:

1. on a real Django worker subprocess: ``celery -A core worker -P prefork -c 1 -Q assozeta-go`` with the
   django-db result backend and CoordinatedTask (the production pool on Linux; ``-P`` overrides the solo pool
   core.celery selects on macOS);
2. on the same reset state, on the race-built Go consumer built with ``-tags consumerprobe`` (which registers the
   same probe names; production builds do not).

Compared after each worker stopped (warm shutdown restores held messages): the TaskResult rows (status, kombu-JSON
result with exception class, module and arguments, meta; timestamps inside the run window), every broker key with
decoded messages (delivery tags, origins and reply_to labelled after a shape check; a retry eta as its offset from
date_done), and, for gated scenarios, the broker while the task runs (the unacked entry acks_late keeps until the
outcome). Authored assertions check that the Django oracle took the intended path.
"""
import base64
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.email_tasks_worker_process import free_port

DATABASE = 'assozeta_contract_consumer_capabilities'
REDIS_PASSWORD = 'private-redis-password-canary'
GO_QUEUE, DJANGO_QUEUE = 'assozeta-go', 'celery'
PROBE, LOST, EARLY = 'consumer_restore.probe', 'consumer_restore.probe_lost', 'consumer_restore.probe_early'
OUTPUT = ROOT / os.environ.get('CONSUMER_CAPABILITIES_OUTPUT', 'quality-reports/go-migration/consumer-restore-capabilities-process')
DJANGO_ONLY = bool(os.environ.get('CELERY_CONSUMER_DJANGO_ONLY'))
ONLY = os.environ.get('CELERY_CONSUMER_ONLY', '')
LIMITS = {'time_limit': 30, 'soft_time_limit': 20}
WORKER_LOST = 'Worker exited prematurely: signal 9 (SIGKILL) Job: %d.'


class Publish:
    def __init__(self, task, mode, value=None, **options):
        self.task, self.mode, self.value, self.options = task, mode, value, options


class Scenario:
    """``expect``: per published message (status, exception class or None, result or exception args)."""

    def __init__(self, name, publishes, expect, *, gate=None, during=None, held=0, timelimit=None):
        self.name, self.publishes, self.expect = name, publishes, expect
        self.gate, self.during, self.held, self.timelimit = gate, during, held, timelimit


SCENARIOS = [
    Scenario('acks_late keeps the message unacknowledged until the result is stored',
             [Publish(PROBE, 'gate', 'g1', **LIMITS)], [('SUCCESS', None, 'g1')], gate='g1', during={'unacked': 1}),
    Scenario('early ack removes the message when the task starts',
             [Publish(EARLY, 'gate', 'g2', **LIMITS)], [('SUCCESS', None, 'g2')], gate='g2', during={'unacked': 0}),
    Scenario('acks_late failure stores FAILURE then acknowledges',
             [Publish(PROBE, 'fail', 'f1', **LIMITS)], [('FAILURE', 'ValueError', ['probe failure', 'f1'])]),
    Scenario('acks_late retry republishes with the time limits and acknowledges',
             [Publish(PROBE, 'retry', **LIMITS)], [('RETRY', 'ValueError', ['probe retry'])], held=1, timelimit=[30, 20]),
    Scenario('worker loss with reject_on_worker_lost requeues the message, redelivered',
             [Publish(PROBE, 'kill-once', **LIMITS)], [('SUCCESS', None, 'redelivered')]),
    Scenario('worker loss of an acks_late task without reject stores WorkerLostError',
             [Publish(LOST, 'kill', **LIMITS)], [('FAILURE', 'WorkerLostError', [WORKER_LOST % 0])]),
    Scenario('worker loss of an early-ack task stores WorkerLostError',
             [Publish(EARLY, 'kill', **LIMITS)], [('FAILURE', 'WorkerLostError', [WORKER_LOST % 0])]),
    Scenario('soft time limit raised in the task and handled',
             [Publish(PROBE, 'soft-catch', time_limit=6, soft_time_limit=1)], [('SUCCESS', None, 'soft')]),
    Scenario('soft time limit not handled fails the task',
             [Publish(PROBE, 'soft-raise', time_limit=6, soft_time_limit=1)], [('FAILURE', 'SoftTimeLimitExceeded', [])]),
    Scenario('hard time limit stores TimeLimitExceeded and the worker continues',
             [Publish(PROBE, 'hard', time_limit=3, soft_time_limit=1), Publish(PROBE, 'value', 'after', **LIMITS)],
             [('FAILURE', 'TimeLimitExceeded', [3]), ('SUCCESS', None, 'after')]),
    Scenario('hard time limit of an early-ack task',
             [Publish(EARLY, 'hard', time_limit=2, soft_time_limit=1)], [('FAILURE', 'TimeLimitExceeded', [2])]),
    Scenario('task default limits apply when the header carries none',
             [Publish(PROBE, 'soft-catch', time_limit=None, soft_time_limit=None)], [('SUCCESS', None, 'soft')],
             timelimit=[None, None]),
    Scenario('a zero soft limit in the header falls back to the task default',
             [Publish(LOST, 'soft-raise', time_limit=6, soft_time_limit=0)], [('FAILURE', 'SoftTimeLimitExceeded', [])],
             timelimit=[6, 0]),
]


class Stack:
    def __init__(self, work, environment, redis_port):
        import redis
        self.work, self.env, self.redis_port = work, environment, redis_port
        self.broker = redis.Redis(host='127.0.0.1', port=redis_port, db=0, password=REDIS_PASSWORD, socket_timeout=10)
        self.probe_dir = work / 'probe'
        self.children = []

    @property
    def broker_url(self):
        return f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.redis_port}'

    def reset(self):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('TRUNCATE django_celery_results_taskresult, instance_datarestore RESTART IDENTITY CASCADE')
        self.broker.flushdb()
        if self.probe_dir.exists():
            shutil.rmtree(self.probe_dir)
        self.probe_dir.mkdir()

    def publish(self, scenario):
        from core.celery import app
        from core.task_routing import go_task_routes
        from TESTS.adapters.django.family_harness import _reset_celery_app
        import TESTS.adapters.go.consumer_restore_probe_tasks  # noqa: F401  (registers the probe tasks)
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES', 'CELERY_RESULT_BACKEND')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (self.broker_url, DJANGO_QUEUE, go_task_routes(','.join((PROBE, LOST, EARLY))),
                                        'django-db'))))
        _reset_celery_app(app)
        payloads = []
        try:
            for item in scenario.publishes:
                self.broker.flushdb()
                app.tasks[item.task].apply_async(args=[item.mode], kwargs={'value': item.value}, **item.options)
                raw = self.broker.rpop(GO_QUEUE)
                assert raw is not None and self.broker.llen(GO_QUEUE) == 0 and self.broker.llen(DJANGO_QUEUE) == 0, 'routed publish'
                payloads.append(raw.decode())
        finally:
            app.conf.update(saved)
            _reset_celery_app(app)
        self.broker.flushdb()
        return payloads

    def push(self, payloads):
        self.broker.flushdb()
        for payload in payloads:
            self.broker.lpush(GO_QUEUE, payload)

    def broker_state(self):
        state = {}
        for key in sorted(self.broker.keys('*')):
            kind = self.broker.type(key).decode()
            name = key.decode('utf-8', 'backslashreplace')
            if kind == 'list':
                state[name] = ['list', [item.decode() for item in self.broker.lrange(key, 0, -1)]]
            elif kind == 'set':
                state[name] = ['set', sorted(item.decode('utf-8', 'backslashreplace') for item in self.broker.smembers(key))]
            elif kind == 'hash':
                state[name] = ['hash', {k.decode(): v.decode() for k, v in self.broker.hgetall(key).items()}]
            elif kind == 'zset':
                state[name] = ['zset', len(self.broker.zrange(key, 0, -1))]
            else:
                state[name] = [kind, None]
        return state

    def results(self):
        from django_celery_results.models import TaskResult
        return {row.task_id: row.status for row in TaskResult.objects.all()}

    def quiescent(self, scenario, ids):
        if self.broker.llen(GO_QUEUE) or self.broker.llen(DJANGO_QUEUE):
            return False
        if self.broker.hlen('unacked') != scenario.held:
            return False
        rows = self.results()
        return all(rows.get(task_id) == expect[0] for task_id, expect in zip(ids, scenario.expect)) and len(rows) == len(ids)


class Harness:
    def __init__(self, stack, binary):
        self.stack, self.binary = stack, binary
        self.completed, self.observations = [], {}

    @staticmethod
    def wait(predicate, description, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError('Timed out: ' + description)

    def gate(self, scenario, process, label):
        """While the gated task runs, record the broker, then release it."""
        if not scenario.gate:
            return None
        started = self.stack.probe_dir / f'started-{scenario.gate}'
        self.wait(lambda: started.exists() or process.poll() is not None, f'{label} gate: ' + scenario.name)
        assert process.poll() is None, f'{label} worker exited before the gate'
        time.sleep(0.3)
        during = {'unacked': self.stack.broker.hlen('unacked'), 'queue': self.stack.broker.llen(GO_QUEUE)}
        (self.stack.probe_dir / f'release-{scenario.gate}').write_text('')
        return during

    def stop(self, process, stream, label):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        try:
            code = process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait(timeout=10)
        stream.close()
        return code

    def run_django(self, scenario, payloads, ids):
        self.stack.push(payloads)
        log = OUTPUT / f'django-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        environment = dict(os.environ, DJANGO_SETTINGS_MODULE='TESTS.adapters.go.consumer_restore_settings',
                           CONSUMER_RESTORE_BROKER_URL=self.stack.broker_url, CONSUMER_PROBE_DIR=str(self.stack.probe_dir),
                           PYTHONPATH=f'{ROOT / "BE"}:{ROOT}', OBJC_DISABLE_INITIALIZE_FORK_SAFETY='YES')
        process = subprocess.Popen([sys.executable, '-m', 'TESTS.adapters.go.consumer_restore_worker', '-c', '1',
                                    '-P', 'prefork', '-Q', GO_QUEUE, '-I', 'TESTS.adapters.go.consumer_restore_probe_tasks', '-n', 'oracle@%h',
                                    '--without-heartbeat', '--without-gossip', '--without-mingle', '-l', 'WARNING'],
                                   cwd=ROOT / 'BE', env=environment, stdout=stream, stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            during = self.gate(scenario, process, 'Django')
            self.wait(lambda: self.stack.quiescent(scenario, ids) or process.poll() is not None, 'Django worker: ' + scenario.name)
            assert process.poll() is None, 'Django worker exited: ' + log.read_text()[-2000:]
            time.sleep(0.5)
        finally:
            code = self.stop(process, stream, 'Django')
        assert code == 0, f'Django worker exit status {code}: {log.read_text()[-2000:]}'
        return during

    def env_file(self):
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(self.stack.work), DBPORT='5432',
                      DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
                      JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
                      SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
                      AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL='http://127.0.0.1:9',
                      AWS_LOCATION='private', REDIS_HOST='127.0.0.1', REDIS_PORT=str(self.stack.redis_port),
                      REDIS_USERNAME='default', REDIS_PASSWORD=REDIS_PASSWORD, GO_TASK_QUEUE_TASKS=','.join((PROBE, LOST, EARLY)),
                      DATA_RESTORE_LOCK_PATH=str(self.stack.work / 'contract-lifecycle.lock'), EMAIL_HOST='127.0.0.1')
        path = self.stack.work / 'consumer.env'
        path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        path.chmod(0o600)
        return path

    def run_go(self, scenario, payloads, ids):
        self.stack.push(payloads)
        log = OUTPUT / f'consumer-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        environment = dict(self.stack.env, CONSUMER_PROBE_DIR=str(self.stack.probe_dir))
        process = subprocess.Popen([str(self.binary), '--env-file', str(self.env_file()), '--concurrency', '1'],
                                   env=environment, stdout=stream, stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            during = self.gate(scenario, process, 'Go')
            self.wait(lambda: self.stack.quiescent(scenario, ids) or process.poll() is not None, 'Go consumer: ' + scenario.name)
            assert process.poll() is None, 'Go consumer exited: ' + log.read_text()[-2000:]
            time.sleep(0.5)
        finally:
            code = self.stop(process, stream, 'Go')
        text = log.read_text()
        assert 'WARNING: DATA RACE' not in text, 'Go race detector: ' + scenario.name
        for private in (REDIS_PASSWORD, 'private-worker-password-canary'):
            assert private not in text, 'Go consumer leaked private configuration'
        assert code == 0, f'Go consumer exit status {code}: {text[-2000:]}'
        return during

    # -- observation --------------------------------------------------------------------------
    def observe(self, payloads, ids, since, until, during):
        from django_celery_results.models import TaskResult
        labels = {task_id: f'task-{i}' for i, task_id in enumerate(ids)}
        originals = [json.loads(p) for p in payloads]
        tags = {m['properties']['delivery_tag']: f'tag-{i}' for i, m in enumerate(originals)}
        rows, reference = [], None
        for row in TaskResult.objects.order_by('task_id').values():
            row = dict(row)
            reference = row['date_done']
            for field in ('date_created', 'date_done'):
                assert since <= row[field] <= until, f'{field} outside the run window'
                row[field] = 'run'
            row.pop('id')
            assert row['task_id'] in labels, 'unexpected task result'
            row['task_id'] = labels[row['task_id']]
            meta = json.loads(row['meta'])
            meta['children'] = [[[labels.get(child, '<new-id>'), rest], extra] for (child, rest), extra in meta['children']]
            row['meta'] = json.dumps(meta)
            rows.append(row)
        rows.sort(key=lambda r: r['task_id'])
        broker = {}
        for key, (kind, value) in self.stack.broker_state().items():
            if kind == 'list':
                value = [self.normalize(item, originals, tags, labels, reference) for item in value]
            elif kind == 'hash':
                value = {tags.get(k, '<new-tag>'): self.normalize_unacked(v, originals, tags, labels, reference)
                         for k, v in value.items()}
            broker[key] = [kind, value]
        return {'task_results': rows, 'broker': broker, 'during': during}

    def normalize_unacked(self, raw, originals, tags, labels, reference):
        message, exchange, routing_key = json.loads(raw)
        return [self.normalize(json.dumps(message), originals, tags, labels, reference), exchange, routing_key]

    @staticmethod
    def normalize(raw, originals, tags, labels, reference):
        message = json.loads(raw)
        headers, properties = message['headers'], message['properties']
        UUID(properties['delivery_tag'])
        properties['delivery_tag'] = tags.get(properties['delivery_tag'], '<new-tag>')
        assert re.fullmatch(r'gen\d+@.+', headers['origin']), headers['origin']
        known = {m['headers']['origin'] for m in originals}
        headers['origin'] = '<original-origin>' if headers['origin'] in known else '<origin>'
        UUID(properties['reply_to'])
        replies = {m['properties']['reply_to'] for m in originals}
        properties['reply_to'] = '<original-reply-to>' if properties['reply_to'] in replies else '<reply-to>'
        for key in ('id', 'root_id', 'parent_id'):
            if headers.get(key) in labels:
                headers[key] = labels[headers[key]]
        if properties.get('correlation_id') in labels:
            properties['correlation_id'] = labels[properties['correlation_id']]
        etas = {m['headers'].get('eta') for m in originals}
        if headers.get('eta') and headers['eta'] not in etas:
            assert reference is not None, 'a new eta without a task result'
            offset = datetime.fromisoformat(headers['eta']).timestamp() - reference.timestamp()
            assert 0 < math.ceil(offset) - offset < 1 or offset == int(offset), offset
            headers['eta'] = 'date_done+%ds' % math.ceil(offset)
        message['body'] = json.loads(base64.b64decode(message['body']))
        return json.dumps(message, ensure_ascii=False)

    # -- scenarios ----------------------------------------------------------------------------
    def execute(self, scenario):
        stack = self.stack
        stack.reset()
        payloads = stack.publish(scenario)
        assert payloads and len(payloads) == len(scenario.publishes), 'no payloads published'
        ids = [json.loads(p)['headers']['id'] for p in payloads]
        since = datetime.now(timezone.utc)
        during = self.run_django(scenario, payloads, ids)
        django = self.observe(payloads, ids, since, datetime.now(timezone.utc), during)
        self.authored(scenario, payloads, django)
        if DJANGO_ONLY:
            self.observations[scenario.name] = {'django': django}
            self.completed.append(scenario.name)
            print('DJANGO: ' + scenario.name, flush=True)
            return
        stack.reset()
        since = datetime.now(timezone.utc)
        during = self.run_go(scenario, payloads, ids)
        go = self.observe(payloads, ids, since, datetime.now(timezone.utc), during)
        self.observations[scenario.name] = {'django': django, 'go': go}
        if go != django:
            (OUTPUT / 'difference.json').write_text(json.dumps({'scenario': scenario.name, 'django': django, 'go': go},
                                                               indent=1, default=str, ensure_ascii=False) + '\n')
        assert go == django, 'Go differs from Django: ' + scenario.name
        self.completed.append(scenario.name)
        print('PASS: ' + scenario.name, flush=True)

    def authored(self, scenario, payloads, observed):
        """The Django oracle took the path the scenario was written for."""
        rows = observed['task_results']
        assert len(rows) == len(scenario.expect), rows
        for index, (status, exc, value) in enumerate(scenario.expect):
            row = rows[index]
            assert row['task_id'] == f'task-{index}' and row['status'] == status, rows
            # Task.retry publishes with apply_async: the same id is added to the request's children.
            children = [[[f'task-{index}', None], None]] if status == 'RETRY' else []
            assert row['meta'] == json.dumps({'children': children}), rows
            result = json.loads(row['result'])
            if exc:
                assert result == {'exc_type': exc, 'exc_message': value, 'exc_module': result['exc_module']}, result
            else:
                assert result == value, result
        if scenario.during is not None:
            assert observed['during'] == dict(scenario.during, queue=0), observed['during']
        queue = observed['broker'].get(GO_QUEUE, ['list', []])[1]
        assert len(queue) == scenario.held, observed['broker']
        assert set(observed['broker']) <= {GO_QUEUE, '_kombu.binding.' + GO_QUEUE, 'unacked_mutex'}, observed['broker']
        if scenario.timelimit is not None:
            assert json.loads(payloads[0])['headers']['timelimit'] == scenario.timelimit, payloads[0]
        if scenario.held:
            held = json.loads(queue[0])
            assert held['headers']['timelimit'] == scenario.timelimit and held['headers']['retries'] == 1, held
            assert held['headers']['eta'] == 'date_done+600s' and held['headers']['redelivered'] is True, held


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
    selected = [s for s in SCENARIOS if not ONLY or ONLY in s.name]
    assert selected, 'no scenario selected'
    harness = None
    with tempfile.TemporaryDirectory(prefix='az-consumer-cap-', dir='/tmp') as directory, (OUTPUT / 'services.log').open('w') as log:
        work = Path(directory)
        attempted = False
        redis_process = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            if not DJANGO_ONLY:
                subprocess.run(['go', 'build', '-race', '-tags', 'consumerprobe', '-o', str(work / 'celery-consumer'),
                                './cmd/celery-consumer'], cwd=ROOT / 'BE_GO', env=environment, stdout=log,
                               stderr=subprocess.STDOUT, check=True, timeout=600)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass',
                                              REDIS_PASSWORD, '--save', '', '--appendonly', 'no', '--dir', str(work)],
                                             stdout=log, stderr=subprocess.STDOUT)
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            stack = Stack(work, environment, redis_port)
            harness = Harness(stack, work / 'celery-consumer')
            for scenario in selected:
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
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    if redis_process:
                        redis_process.terminate()
                        redis_process.wait(timeout=10)
                    if attempted:
                        try:
                            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                        except subprocess.CalledProcessError:
                            if (work / 'postgres/postmaster.pid').exists():
                                raise
                    report.write_text(json.dumps(result, indent=2) + '\n')
    assert harness and harness.completed == [s.name for s in selected], 'not every scenario completed'
    paths = ['TESTS/adapters/go/celery_consumer_capabilities_process.py', 'TESTS/adapters/go/consumer_restore_probe_tasks.py',
             'TESTS/adapters/go/consumer_restore_settings.py', 'BE/core/celery.py', 'BE/instance/restore/task_base.py']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'internal/celeryworker/', 'cmd/celery-consumer/')))
    result.update(success=True, partial=bool(ONLY), django_only=DJANGO_ONLY, race_instrumented=not DJANGO_ONLY,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_hashes', 'scope')}))


if __name__ == '__main__':
    main()
