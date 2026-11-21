"""Phase 5 harness: a simulated installation whose runtime switch is real.

Builds on t-p0's TESTS/handoff/topology.py and t-p2's p2_harness.py (imported, not
copied). Containers are simulated (TESTS/handoff/p5_docker.py: real `docker compose
config`, simulated up/run/stop/ps), but everything a runtime switch decides with is
real and private to the test run:

  - PostgreSQL: a throwaway cluster (Unix socket, trust auth, fsync off) with every
    operational migration in BE_GO/migrations applied as SQL (template `p5_current`)
    or only the historical ones <= 000004 (template `p5_legacy`, what a 2284ed46
    installation has), plus minimal stand-ins for the three Django tables the
    switch and the adoption read (instance_instanceconfiguration,
    communications_whatsappdelivery, auth-free). Each installation gets a fresh copy.
  - Redis: a private redis-server on 127.0.0.1 with a random password; the
    installation's .env carries that password so the controller authenticates with
    the installation's own credentials.
  - The updater tools: `docker run <ASSOZETA_UPDATER_REF> /runner/X.py ...` runs the
    checkout's selfhost/updater/X.py on the host (ASSOZETA_HANDOFF_UPDATER_DIR
    overrides the directory for reference/mutation runs).

`P5Installation` installs a distribution with its own CLI, then drives the
controller in-process (runtime_switch.submit/run/recover/status with os.environ
pointing at the stand-in) or as the operator would (bin/assozeta runtime-*,
python3 selfhost/updater/runtime_switch.py ...). Work is seeded the way correct
producers leave it: Celery protocol-2 envelopes on the historical queues (with a
durable admission through public.assozeta_handoff_admit where the schema has it)
and Go durable rows in public.assozeta_go_jobs.

No timed sleep decides an outcome: tests read durable records (authority row,
ledger, transfers, Redis lists, the journal, the simulated container event log
whose entries are stamped with the authority and maintenance flag of the instant).
"""
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from TESTS.handoff.p2_harness import (ACTIVE_API, APP, DJANGO_APP, GO_APP, SHARED, TOOLS, ModeInstallation, render)
from TESTS.handoff.topology import HERE, ROOT

UPDATER = Path(os.environ.get('ASSOZETA_HANDOFF_UPDATER_DIR') or ROOT / 'selfhost/updater').resolve()
MIGRATIONS = ROOT / 'BE_GO/migrations'
USER = 'contracts'
UPDATER_REF = 'assozeta-updater:p5-fixture'
OWNER = '6f2b7c1e-3d4a-4e5f-8a9b-0c1d2e3f4a5b'
OTHER_OWNER = '7a3c8d2f-4e5b-4f60-9b0c-1d2e3f4a5b6c'
PRIORITY_SEPARATOR = '\x06\x16'
QUEUE = {'django': 'celery', 'go': 'assozeta-go'}
MAIL_TASK = 'core.tasks.send_mail_async'
DB_TASK = 'reset_communication_daily_email_balance'
FLAG = '.operations/maintenance.flag'
PROGRESS_STAGES = ('preflight', 'close_admission', 'settle', 'stage_backlog', 'stop_source', 'target_preflight',
                   'commit', 'activate', 'verify')
LEGACY_KNOBS = ('ASSOZETA_GO_TASKS', 'GO_TASK_QUEUE_TASKS', 'ASSOZETA_REALTIME_RELAY_BACKEND',
                'ASSOZETA_DJANGO_RELAY_REPLICAS', 'ASSOZETA_GO_RELAY_REPLICAS', 'ASSOZETA_BAKNEY_PROTOCOL_BACKEND',
                'ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM')
DJANGO_STUBS = '''
CREATE TABLE IF NOT EXISTS public.instance_instanceconfiguration (
    id integer PRIMARY KEY, name text, abbreviation text, domain text, self_hosted boolean NOT NULL DEFAULT true,
    setup_provenance text, ui_api_version varchar(2) NOT NULL DEFAULT 'v1',
    go_tasks_enabled boolean NOT NULL DEFAULT false, primary_association_id uuid,
    configured_at timestamptz, updated_at timestamptz);
CREATE TABLE IF NOT EXISTS public.communications_whatsappdelivery (
    id uuid PRIMARY KEY, status text NOT NULL, attempts integer NOT NULL DEFAULT 0, body text, recipient text,
    event_type text, template_name text, template_language text, error_code text, error_message text,
    provider_message_id text, next_attempt_at timestamptz, created_at timestamptz, updated_at timestamptz);
'''


class HarnessError(RuntimeError):
    """The harness itself failed (never a valid red)."""


def wait_for(predicate, description, timeout=60.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f'P5 contract red: timed out waiting for {description}')
        time.sleep(interval)


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def postgres_binaries():
    found = shutil.which('postgres')
    if found:
        return Path(found).parent
    for pattern in ('opt/homebrew/opt/postgresql*/bin/postgres', 'usr/lib/postgresql/*/bin/postgres',
                    'usr/local/opt/postgresql*/bin/postgres'):
        candidates = sorted(Path('/').glob(pattern))
        if candidates:
            return candidates[-1].parent
    raise HarnessError('PostgreSQL server binaries (initdb, pg_ctl, postgres, psql) are required; never skipped')


class Resp:
    """A minimal RESP client for fixtures and observations (independent of the code under test)."""

    def __init__(self, port, password):
        self.port, self.password = port, password

    def call(self, *arguments):
        with socket.create_connection(('127.0.0.1', self.port), 10) as sock, sock.makefile('rb') as stream:
            def send(*parts):
                frame = [b'*%d\r\n' % len(parts)]
                for part in parts:
                    data = part if isinstance(part, bytes) else str(part).encode()
                    frame += [b'$%d\r\n' % len(data), data, b'\r\n']
                sock.sendall(b''.join(frame))
                return self.reply(stream)
            send('AUTH', 'default', self.password)
            return send(*arguments)

    def reply(self, stream):
        line = stream.readline()
        kind, rest = line[:1], line[1:-2]
        if kind == b'+':
            return rest.decode()
        if kind == b'-':
            raise HarnessError('redis: ' + rest.decode())
        if kind == b':':
            return int(rest)
        if kind == b'$':
            size = int(rest)
            if size < 0:
                return None
            data = stream.read(size + 2)[:-2]
            return data
        if kind == b'*':
            size = int(rest)
            return None if size < 0 else [self.reply(stream) for _ in range(size)]
        raise HarnessError('redis: unexpected reply')


class Services:
    """One private PostgreSQL cluster and one private Redis for the whole test module."""

    def __init__(self):
        self.bin = postgres_binaries()
        self.psql = str(self.bin / 'psql') if (self.bin / 'psql').exists() else shutil.which('psql')
        redis = shutil.which('redis-server')
        if not self.psql or not redis:
            raise HarnessError('psql and redis-server are required; never skipped')
        self.redis_server = redis
        # Short path: the Unix socket path limit is ~100 bytes on macOS.
        self.directory = Path(tempfile.mkdtemp(prefix='az-p5-', dir='/tmp'))
        self.redis_password = secrets.token_hex(16)
        self.redis_port = free_port()
        self.counter = 0
        self.lock = threading.Lock()
        self.pg_started = False
        self.redis_process = None

    def _run(self, *args, **kwargs):
        result = subprocess.run([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                timeout=kwargs.pop('timeout', 120), **kwargs)
        if result.returncode:
            raise HarnessError(f'{Path(str(args[0])).name} failed: {result.stdout[-1500:]}')
        return result.stdout

    def start(self):
        data = self.directory / 'pg'
        self._run(self.bin / 'initdb', '-D', data, '-A', 'trust', '-U', USER, '--no-sync', '--no-locale', '-E', 'UTF8')
        self._run(self.bin / 'pg_ctl', '-D', data, '-l', self.directory / 'postgres.log', '-o',
                  f"-k {self.directory} -h '' -F -c fsync=off -c max_connections=200", '-w', 'start')
        self.pg_started = True
        self.migrations = sorted(MIGRATIONS.glob('0*_*.up.sql'))
        for template, limit in (('p5_current', None), ('p5_legacy', 4)):
            self._run(self.bin / 'createdb', '-h', self.directory, '-U', USER, template)
            self.query(template, DJANGO_STUBS)
            self.apply(template, upto=limit)
        self.redis_process = subprocess.Popen(
            [self.redis_server, '--port', str(self.redis_port), '--bind', '127.0.0.1', '--requirepass',
             self.redis_password, '--save', '', '--appendonly', 'no', '--dir', str(self.directory)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.redis = Resp(self.redis_port, self.redis_password)

        def ready():
            try:
                return self.redis.call('PING') == 'PONG'
            except (OSError, HarnessError):
                return False
        wait_for(ready, 'private Redis startup', timeout=30)
        return self

    def apply(self, database, upto=None, after=0):
        versions = []
        for path in self.migrations:
            version = int(path.name.split('_', 1)[0])
            if version <= after or (upto is not None and version > upto):
                continue
            self._run(self.psql, '-h', self.directory, '-U', USER, '-d', database, '-X', '-q', '-v', 'ON_ERROR_STOP=1',
                      '-f', path)
            versions.append(version)
        if versions:
            self.query(database, 'CREATE TABLE IF NOT EXISTS public.assozeta_go_schema_migrations '
                                 '(version bigint NOT NULL PRIMARY KEY, dirty boolean NOT NULL);'
                                 'DELETE FROM public.assozeta_go_schema_migrations;'
                                 f'INSERT INTO public.assozeta_go_schema_migrations VALUES ({max(versions)}, false);')
        return versions

    def current_version(self):
        return max(int(path.name.split('_', 1)[0]) for path in self.migrations)

    def fresh_database(self, legacy=False):
        with self.lock:
            self.counter += 1
            name = f'p5_{self.counter}'
        self._run(self.bin / 'createdb', '-h', self.directory, '-U', USER, '-T',
                  'p5_legacy' if legacy else 'p5_current', name)
        return name

    def psql_command(self, database):
        return [self.psql, '-h', str(self.directory), '-U', USER, '-d', database, '-X', '-q', '-At',
                '-v', 'ON_ERROR_STOP=1']

    def query(self, database, sql):
        result = subprocess.run(self.psql_command(database), input=sql, text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise HarnessError(f'fixture SQL failed: {result.stderr[-800:]}')
        return result.stdout.strip()

    def rows(self, database, sql):
        return json.loads(self.query(database, f"SELECT coalesce(json_agg(r), '[]') FROM ({sql}) r;") or '[]')

    def stop(self):
        if self.redis_process and self.redis_process.poll() is None:
            self.redis_process.terminate()
            try:
                self.redis_process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.redis_process.kill()
        if self.pg_started:
            subprocess.run([str(self.bin / 'pg_ctl'), '-D', str(self.directory / 'pg'), '-m', 'immediate', '-w', 'stop'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        shutil.rmtree(self.directory, ignore_errors=True)


SERVICES = None


def services():
    global SERVICES
    if SERVICES is None:
        SERVICES = Services().start()
    return SERVICES


def stop_services():
    global SERVICES
    if SERVICES is not None:
        SERVICES.stop()
        SERVICES = None


# -- Celery protocol 2 envelopes as kombu's Redis transport stores them ---------------------------------------------
def envelope(task, task_id, *, args=(), kwargs=None, queue='celery', eta=None, priority=0, stamp=None, retries=0):
    """A Celery protocol 2 JSON message (stdlib only; same fields as TESTS/handoff/p3_stack.Stack.envelope)."""
    body = base64.b64encode(json.dumps([list(args), kwargs or {}, {'callbacks': None, 'errbacks': None, 'chain': None,
                                                                    'chord': None}]).encode()).decode()
    headers = {'lang': 'py', 'task': task, 'id': task_id, 'shadow': None,
               'eta': eta.astimezone(timezone.utc).isoformat() if eta else None, 'expires': None, 'group': None,
               'group_index': None, 'retries': retries, 'timelimit': [None, None], 'root_id': task_id,
               'parent_id': None, 'argsrepr': repr(tuple(args)), 'kwargsrepr': repr(kwargs or {}),
               'origin': 'gen1@p5-fixture', 'ignore_result': False, 'replaced_task_nesting': 0,
               'stamped_headers': None, 'stamps': {},
               'assozeta_published_at': stamp if stamp is not None else int(time.time())}
    message = {'body': body, 'content-encoding': 'utf-8', 'content-type': 'application/json', 'headers': headers,
               'properties': {'correlation_id': task_id, 'reply_to': str(uuid.uuid4()), 'delivery_mode': 2,
                              'delivery_info': {'exchange': '', 'routing_key': queue}, 'priority': priority,
                              'body_encoding': 'base64', 'delivery_tag': str(uuid.uuid4())}}
    return json.dumps(message).encode()


def mail_args(n):
    return (f'Fixture subject {n}', 'Synthetic body', 'sender@example.invalid', [f'recipient{n}@example.invalid'])


def priority_key(queue, priority):
    step = max([0] + [s for s in (3, 6, 9) if priority >= s])
    return queue if step == 0 else f'{queue}{PRIORITY_SEPARATOR}{step}'


class P5Installation(ModeInstallation):
    """A disposable installation (current distribution or `revision`) on the private services."""

    def __init__(self, directory, revision=None, legacy_database=False):
        super().__init__(directory, revision=revision)
        self.services = services()
        self.database = self.services.fresh_database(legacy=legacy_database)
        self.services.redis.call('FLUSHALL')
        self._tool('docker', f'exec "{sys.executable}" -I "{HERE / "p5_docker.py"}" "$@"\n')
        self._module = None

    # -- environment ----------------------------------------------------------------------------------------------
    def environment(self, **extra):
        return super().environment(
            HANDOFF_P5_PSQL=json.dumps(self.services.psql_command(self.database)),
            HANDOFF_P5_REDIS=f'127.0.0.1:{self.services.redis_port}', HANDOFF_P5_UPDATER=str(UPDATER),
            HANDOFF_P5_PYTHONPATH=str(ROOT / 'BE/instance'), HANDOFF_P5_FLAG=str(self.root / FLAG), **extra)

    def install(self):
        result = super().install()
        self.set_env(ASSOZETA_UPDATER_REF=UPDATER_REF, REDIS_USERNAME='default',
                     REDIS_PASSWORD=self.services.redis_password)
        return result

    @contextlib.contextmanager
    def active(self):
        """os.environ as a process started by this installation's CLI sees it (for in-process calls)."""
        saved = dict(os.environ)
        environment = self.environment()
        os.environ.clear()
        os.environ.update({**saved, **environment})
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(saved)

    # -- controller -----------------------------------------------------------------------------------------------
    def rs(self, *names):
        for path in (str(UPDATER), str(ROOT / 'BE/instance')):
            if path not in sys.path:
                sys.path.insert(0, path)
        module = importlib.import_module('runtime_switch')
        missing = [name for name in names if not hasattr(module, name)]
        if missing:
            raise AssertionError('P5 contract red: CONTRACT MISSING runtime_switch.' + ', runtime_switch.'.join(missing))
        return module

    def journal(self):
        self.rs()
        from journal import Journal
        return Journal(self.root / '.updater/operations.sqlite3')

    def body(self, target, generation=None, key=None, actor=OWNER):
        generation = self.authority()[1] if generation is None else generation
        return {'request_id': key or str(uuid.uuid4()), 'actor_id': actor, 'target': target,
                'expected_generation': generation}

    def submit(self, target, generation=None, key=None, actor=OWNER):
        module = self.rs('submit')
        with self.active():
            return module.submit(self.root, self.env_file, self.journal(), None,
                                 self.body(target, generation, key, actor))

    def run(self, operation_id, barrier=None):
        module = self.rs('run')
        record = self.record(operation_id)
        with self.active():
            return module.run(self.root, self.env_file, self.journal(), None, record, steps=None, barrier=barrier)

    def recover(self, barrier=None):
        module = self.rs('recover')
        with self.active():
            return module.recover(self.root, self.env_file, self.journal(), None, steps=None, barrier=barrier)

    def status(self):
        module = self.rs('status')
        with self.active():
            return module.status(self.root, self.env_file, self.journal(), None)

    def switch(self, target, barrier=None, generation=None):
        """Submit as the API relays it (runner POST /runtime-switches) and run as the runner work loop does."""
        code, payload = self.submit(target, generation)
        if code != 202:
            raise AssertionError(f'P5 contract red: switch to {target} not admitted: HTTP {code} {payload}')
        return self.run(payload['operation']['id'], barrier=barrier)

    def record(self, operation_id):
        return next(r for r in self.journal().records() if r['id'] == operation_id)

    def runtime_records(self):
        return [r for r in self.journal().records() if r.get('kind') == 'runtime_switch']

    def tool(self, *arguments, extra_env=None, timeout=600):
        """python3 selfhost/updater/runtime_switch.py ARGS as the updater image runs it (neither app API involved)."""
        environment = self.environment(PYTHONPATH=str(ROOT / 'BE/instance'), **(extra_env or {}))
        return subprocess.run([sys.executable, '-B', str(UPDATER / 'runtime_switch.py'), *arguments],
                              env=environment, capture_output=True, text=True, timeout=timeout, cwd=self.root)

    def tool_json(self, *arguments, **kwargs):
        result = self.tool(*arguments, **kwargs)
        try:
            return result, json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return result, None

    def popen_tool(self, *arguments, extra_env=None):
        environment = self.environment(PYTHONPATH=str(ROOT / 'BE/instance'), **(extra_env or {}))
        return subprocess.Popen([sys.executable, '-B', str(UPDATER / 'runtime_switch.py'), *arguments],
                                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=self.root, start_new_session=True)

    # -- durable observations -------------------------------------------------------------------------------------
    def sql(self, statement):
        return self.services.query(self.database, statement)

    def rows(self, statement):
        return self.services.rows(self.database, statement)

    def has_table(self, name):
        return self.sql(f"SELECT to_regclass('public.{name}') IS NOT NULL;") == 't'

    def authority(self):
        line = self.sql('SELECT runtime, generation, operation_id FROM public.assozeta_go_job_ownership WHERE singleton=1;')
        runtime, generation, operation = line.split('|')
        return runtime, int(generation), operation or None

    def admission(self):
        if self.sql("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name="
                    "'assozeta_go_job_ownership' AND column_name='admission');") != 't':
            return 'open'
        return self.sql('SELECT admission FROM public.assozeta_go_job_ownership WHERE singleton=1;')

    def maintenance(self):
        return (self.root / FLAG).exists()

    def events(self):
        return self.observed()['events']

    def mark(self):
        return len(self.events())

    def changes(self, since=0, until=None):
        """Container-changing events in [since, until), each with its authority/maintenance stamp."""
        out = []
        for event in self.events()[since:until]:
            call = event.get('call', [])
            if call[:1] == ['compose'] and len(call) > 1 and call[1] in ('up', 'run', 'stop', 'kill', 'rm', 'down',
                                                                        'start', 'restart', 'create'):
                out.append(event)
            elif call[:1] in (['run'], ['exec'], ['rm'], ['stop'], ['kill']) and len(call) > 1:
                out.append(event)
        return out

    def oneoffs_running(self):
        return self.observed().get('oneoffs_running', [])

    def fail_oneoff(self, *services_):
        state = self.observed()
        state['fail_oneoff'] = sorted(services_)
        self.state.write_text(json.dumps(state, indent=1, sort_keys=True))

    def broker(self):
        return self.services.redis

    def queue_ids(self, queue):
        """{key: [task ids]} for the queue and its priority subqueues, plus unacked copies."""
        found = {}
        for suffix in ('', f'{PRIORITY_SEPARATOR}3', f'{PRIORITY_SEPARATOR}6', f'{PRIORITY_SEPARATOR}9'):
            items = self.broker().call('LRANGE', queue + suffix, 0, -1) or []
            if items:
                found[queue + suffix] = [json.loads(raw)['headers']['id'] for raw in items]
        unacked = self.broker().call('HGETALL', 'unacked') or []
        for value in unacked[1::2]:
            message, _, origin = json.loads(value)
            if origin == queue:
                found.setdefault('unacked', []).append(message['headers']['id'])
        return found

    def copies(self, task_id):
        """{queue key: count} of broker copies of task_id on both historical queues."""
        result = {}
        for queue in ('celery', 'assozeta-go'):
            for key, ids in self.queue_ids(queue).items():
                n = ids.count(task_id)
                if n:
                    result[key] = n
        return result

    def broker_message(self, task_id):
        for queue in ('celery', 'assozeta-go'):
            for suffix in ('', f'{PRIORITY_SEPARATOR}3', f'{PRIORITY_SEPARATOR}6', f'{PRIORITY_SEPARATOR}9'):
                for raw in self.broker().call('LRANGE', queue + suffix, 0, -1) or []:
                    message = json.loads(raw)
                    if message['headers']['id'] == task_id:
                        return queue + suffix, message
        return None, None

    def ledger(self, task_id):
        if not self.has_table('assozeta_handoff_work'):
            return None
        rows = self.rows(f"SELECT task_id, task_name, transport, queue, state, owner_runtime, owner_generation, "
                         f"operation_id, eta, block_reason, accepted_at FROM public.assozeta_handoff_work "
                         f"WHERE task_id='{task_id}'")
        return rows[0] if rows else None

    def go_job(self, task_id):
        rows = self.rows(f"SELECT id::text, kind, state, error_code, available_at, attempts FROM public.assozeta_go_jobs "
                         f"WHERE id::text='{task_id}'")
        return rows[0] if rows else None

    def transfers(self, operation_id):
        if not self.has_table('assozeta_handoff_transfers'):
            return []
        return self.rows(f"SELECT task_id, source_runtime, source_generation, target_runtime, target_generation, "
                         f"delivered_at, delivery FROM public.assozeta_handoff_transfers "
                         f"WHERE operation_id='{operation_id}' ORDER BY task_id")

    # -- seeding (what correct producers leave behind) ------------------------------------------------------------
    def push(self, queue, task, task_id=None, *, args=(), eta=None, priority=0, admit=True, kwargs=None, retries=0):
        task_id = task_id or str(uuid.uuid4())
        payload = envelope(task, task_id, args=args, queue=queue, eta=eta, priority=priority, kwargs=kwargs,
                           retries=retries)
        runtime = 'go' if queue == 'assozeta-go' else self.authority()[0]
        if admit and self.has_table('assozeta_handoff_work'):
            outcome = self.sql(
                f"SELECT outcome FROM public.assozeta_handoff_admit('{runtime}', '{task_id}', '{task}', 'broker', "
                f"'{queue}', decode('{base64.b64encode(payload).decode()}', 'base64'), clock_timestamp(), "
                f"{repr_ts(eta)}, NULL);")
            if outcome != 'accepted':
                raise HarnessError(f'fixture admission of {task_id} answered {outcome}')
        self.broker().call('SADD', '_kombu.binding.' + queue, f'{queue}{PRIORITY_SEPARATOR}{PRIORITY_SEPARATOR}{queue}')
        self.broker().call('LPUSH', priority_key(queue, priority), payload)
        return task_id

    def go_row(self, kind=DB_TASK, task_id=None, available_at=None):
        """A Go durable job as repository.Jobs.Enqueue leaves it (admitted when the ledger exists)."""
        task_id = task_id or str(uuid.uuid4())
        due = repr_ts(available_at) if available_at else 'clock_timestamp()'
        self.sql(f"INSERT INTO public.assozeta_go_jobs (id, association_id, kind, version, idempotency_key, payload, "
                 f"max_attempts, available_at) VALUES ('{task_id}', '00000000-0000-0000-0000-000000000000', '{kind}', 1, "
                 f"'p5:{task_id}', '{{}}', 3, {due});")
        return task_id

    def seed_instance(self, ui_api_version='v1', go_tasks_enabled=False):
        self.sql("DELETE FROM public.instance_instanceconfiguration;"
                 "INSERT INTO public.instance_instanceconfiguration (id, name, abbreviation, domain, self_hosted, "
                 f"setup_provenance, ui_api_version, go_tasks_enabled, configured_at, updated_at) VALUES (1, 'P5 fixture', "
                 f"'P5', 'localhost', true, 'fixture', '{ui_api_version}', {str(go_tasks_enabled).lower()}, "
                 "clock_timestamp(), clock_timestamp());")

    def seed_whatsapp(self, status='queued'):
        identifier = str(uuid.uuid4())
        self.sql("INSERT INTO public.communications_whatsappdelivery (id, status, attempts, body, recipient, event_type, "
                 f"next_attempt_at, created_at, updated_at) VALUES ('{identifier}', '{status}', 0, 'Fixture message.', "
                 "'+12025550123', 'subscription_approved', clock_timestamp(), clock_timestamp(), clock_timestamp());")
        return identifier

    def register_consumer(self, runtime, role='broker'):
        """A live source consumer that heartbeats and never acknowledges a freeze (until stopped)."""
        consumer = str(uuid.uuid4())
        stop = threading.Event()

        def beat():
            while not stop.is_set():
                try:
                    self.sql(f"SELECT * FROM public.assozeta_handoff_heartbeat('{consumer}', '{runtime}', '{role}');")
                except HarnessError:
                    pass
                stop.wait(0.3)
        self.sql(f"SELECT * FROM public.assozeta_handoff_heartbeat('{consumer}', '{runtime}', '{role}');")
        thread = threading.Thread(target=beat, daemon=True)
        thread.start()

        def halt():
            stop.set()
            thread.join(10)
        return consumer, halt

    # -- process sets ---------------------------------------------------------------------------------------------
    def app_running(self):
        return {name for name in self.running() if name not in SHARED}

    def assert_mode(self, case, runtime, label=''):
        """Exactly the runtime's resident set runs, the other set does not, shared services run (positive control)."""
        running = set(self.running())
        app = running - SHARED
        case.assertTrue(SHARED <= running, f'P5 contract red: shared services stopped {label}: {sorted(SHARED - running)}')
        case.assertEqual(sorted(app), sorted(APP[runtime]),
                         f'P5 contract red: application services after {label} are not exactly the {runtime} set')

    def env_text(self):
        return self.env_file.read_text()


def repr_ts(value):
    if value is None:
        return 'NULL'
    return f"'{value.astimezone(timezone.utc).isoformat()}'::timestamptz"


def future(days=30):
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)


@contextlib.contextmanager
def installation(case, revision=None, legacy_database=False, install=True):
    directory = tempfile.TemporaryDirectory(prefix='assozeta-p5-')
    case.addCleanup(directory.cleanup)
    item = P5Installation(Path(directory.name), revision=revision, legacy_database=legacy_database)
    if install:
        item.install()
    yield item


class ContractCase:
    """Mixin: every assertion failure of a Phase 5 contract test reads 'P5 contract red: ...'.

    That prefix is the expected-red signature (TESTS/handoff/expected_red.json); a harness
    failure raises HarnessError instead (an error, never a valid red)."""

    def _formatMessage(self, msg, standardMsg):
        text = super()._formatMessage(msg, standardMsg)
        return text if text.startswith('P5 contract red') else 'P5 contract red: ' + text


class SimulatedKill(BaseException):
    """Process death at a barrier: the controller may only catch Exception."""


def killer(point):
    seen = []

    def barrier(reached):
        seen.append(reached)
        if reached == point:
            raise SimulatedKill(point)
    barrier.seen = seen
    return barrier
