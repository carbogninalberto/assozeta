"""Phase 3 handoff stack: private PostgreSQL, Redis, real Celery/Kombu and compiled Go processes.

Runs inside the scenario driver (TESTS/handoff/p3_driver.py) on the production Python
interpreter with the BE requirements installed. Everything is private and disposable:

- PostgreSQL: a throwaway cluster (Unix socket only, trust auth, fsync off) holding the
  current Django schema (``migrate --run-syncdb`` without migration files, as the Go
  harnesses do) plus every operational migration in BE_GO/migrations applied as SQL, with
  ``assozeta_go_schema_migrations`` recording the highest version (what the Go worker's
  schema check reads).
- Redis: a private ``redis-server`` on 127.0.0.1 with a password and no persistence.
- Django: this process is the Django *producer* (``Task.apply_async`` on the private broker);
  Django *workers* are real ``celery worker -P prefork -c 1`` subprocesses
  (TESTS.adapters.go.consumer_restore_worker) on TESTS.handoff.p3_django_settings.
- Go: ``cmd/worker`` (the durable worker, race build) and ``cmd/handoff-probe`` (a Go
  durable producer calling the production ``repository.Jobs.Enqueue``), both built with
  ``-tags handoff`` so the test-only barrier hooks are compiled in.
- Effects: a statement-level trigger on ``communications_communicationconfiguration``
  records every UPDATE statement with the connection's ``application_name`` into
  ``handoff_test_effects``. The database-only task of this slice
  (``reset_communication_daily_email_balance``) issues exactly one such statement per
  execution on either runtime, and a rolled-back (fenced) execution leaves no row.

Barriers (FileBarrier) follow the file protocol pinned in test_p3_database_job's docstring.
No timed sleep decides which side of a commit ran; waits only poll for durable evidence.
"""
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
DATABASE = 'assozeta_contract_handoff'
USER = 'contracts'
# Random per run: private disposable services only (also used to prove no log leaks them).
REDIS_PASSWORD = secrets.token_hex(16)
DB_PASSWORD = secrets.token_hex(16)
DJANGO_WORKER_APP = 'handoff-django-worker'
DRIVER_APP = 'handoff-driver'
GO_WORKER_APP = 'assozeta-go-worker'
GO_PRODUCER_APP = 'handoff-go-producer'
DB_TASK = 'reset_communication_daily_email_balance'
NIL = '00000000-0000-0000-0000-000000000000'
HANDOFF_TABLES = ('assozeta_handoff_attempts', 'assozeta_handoff_transfers', 'assozeta_handoff_consumers',
                  'assozeta_handoff_work')


class ContractFailure(AssertionError):
    """A Phase 3 contract assertion (the only acceptable red)."""


def check(condition, message):
    if not condition:
        raise ContractFailure(message)


def wait_for(predicate, description, timeout=30.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise ContractFailure('Timed out: ' + description)
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
    raise RuntimeError('PostgreSQL server binaries are required; this integration is never skipped')


class FileBarrier:
    """Test side of the file barrier protocol (see test_p3_database_job)."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def reset(self):
        for path in self.directory.iterdir():
            path.unlink()

    def hold(self, point, key='all'):
        (self.directory / f'hold.{point}.{key}').write_text('')

    def release(self, point, key='all'):
        (self.directory / f'release.{point}.{key}').write_text('')

    def arrival(self, point, key):
        path = self.directory / f'arrived.{point}.{key}'
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def arrivals(self, point):
        found = {}
        for path in self.directory.glob(f'arrived.{point}.*'):
            with suppress(OSError, ValueError):
                found[path.name[len(f'arrived.{point}.'):]] = json.loads(path.read_text())
        return found

    def wait_arrived(self, point, key, who, timeout=30.0):
        return wait_for(lambda: self.arrival(point, key), f'{who} never passed barrier {point} for {key} '
                        f'(contract: file barrier hooks in -tags handoff / ASSOZETA_HANDOFF_BARRIER_DIR builds)',
                        timeout=timeout)


class Stack:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        # Short path: the Unix socket path limit is ~100 bytes on macOS.
        self.work = Path(tempfile.mkdtemp(prefix='az-p3-', dir='/tmp'))
        self.barrier = FileBarrier(self.work / 'barriers')
        self.children = []
        self.bin = postgres_binaries()
        self.psql = str(self.bin / 'psql') if (self.bin / 'psql').exists() else shutil.which('psql')
        self.redis_process = None
        self.pg_started = False
        self.log = (self.output / 'services.log').open('w')
        self.env = {}

    # -- lifecycle ------------------------------------------------------------------------------
    def run(self, args, **kwargs):
        return subprocess.run([str(a) for a in args], check=True, stdout=self.log, stderr=subprocess.STDOUT,
                              timeout=kwargs.pop('timeout', 120), **kwargs)

    def start(self):
        base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE',
                                                              'GOTOOLCHAIN', 'GOFLAGS', 'GOPATH')}
        base.update(LC_ALL='C', LANG='C')
        self.base_env = base
        redis_server = shutil.which('redis-server')
        if not redis_server or not shutil.which('go') or not self.psql:
            raise RuntimeError('redis-server, Go and PostgreSQL (with psql) are required; never skipped')
        self.redis_port = free_port()
        self.redis_process = subprocess.Popen(
            [redis_server, '--port', str(self.redis_port), '--bind', '127.0.0.1', '--requirepass', REDIS_PASSWORD,
             '--save', '', '--appendonly', 'no', '--dir', str(self.work)], stdout=self.log, stderr=subprocess.STDOUT)
        data = self.work / 'pg'
        self.run([self.bin / 'initdb', '-D', data, '-A', 'trust', '-U', USER, '--no-sync', '--no-locale', '-E', 'UTF8'],
                 env=base)
        self.run([self.bin / 'pg_ctl', '-D', data, '-l', self.work / 'postgres.log', '-o',
                  f"-k {self.work} -h '' -F -c fsync=off -c max_connections=200", '-w', 'start'], env=base)
        self.pg_started = True
        self.run([self.bin / 'createdb', '-h', self.work, '-U', USER, DATABASE], env=base)
        self.build_go()
        self.init_django()
        self.apply_operational_migrations()
        self.install_effect_counter()
        import redis
        self.redis = redis.Redis(host='127.0.0.1', port=self.redis_port, password=REDIS_PASSWORD, db=0, socket_timeout=10)
        wait_for(lambda: self._redis_ready(), 'private Redis startup', timeout=20)
        return self

    def _redis_ready(self):
        try:
            return self.redis.ping()
        except Exception:
            return False

    def build_go(self):
        env = dict(self.base_env)
        for name, package, race in (('worker', './cmd/worker', True), ('handoff-probe', './cmd/handoff-probe', False)):
            command = ['go', 'build', '-tags', 'handoff', *(['-race'] if race else []), '-o', str(self.work / name), package]
            self.run(command, cwd=ROOT / 'BE_GO', env=env, timeout=600)

    def django_environment(self):
        values = {}
        for line in (ROOT / 'selfhost/.env.example').read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        values.update(self.base_env)
        values.update(
            DJANGO_SETTINGS_MODULE='TESTS.handoff.p3_django_settings', TESTS_DATABASE=DATABASE, TESTS_WORKDIR=str(self.work),
            DBTYPE='POSTGRES', DBHOST=str(self.work), DBPORT='5432', DBNAME=DATABASE, DBUSER=USER, DBPASSWORD=DB_PASSWORD,
            USE_PGBOUNCER='False', STORE_ON_DISK='True', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
            JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()).decode(),
            JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,
                                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
            GO_TASK_QUEUE_TASKS='', ASSOZETA_HANDOFF_BROKER_URL=f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.redis_port}/0',
            ASSOZETA_HANDOFF_BARRIER_DIR=str(self.barrier.directory), ASSOZETA_HANDOFF_APPLICATION=DRIVER_APP,
            PYTHONPATH=f'{ROOT / "BE"}:{ROOT}', OBJC_DISABLE_INITIALIZE_FORK_SAFETY='YES')
        return values

    def init_django(self):
        import contextlib
        from unittest.mock import patch
        self.env = self.django_environment()
        os.environ.clear()
        os.environ.update(self.env)
        sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
        import django
        with contextlib.redirect_stdout(self.log), contextlib.redirect_stderr(self.log), patch('environ.Env.read_env'):
            django.setup()
            from django.apps import apps
            from django.conf import settings
            from django.core.management import call_command
            settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
            call_command('migrate', interactive=False, verbosity=0, run_syncdb=True, stdout=self.log, stderr=self.log)
        import psycopg2
        self.connection = psycopg2.connect(host=str(self.work), user=USER, dbname=DATABASE, application_name=DRIVER_APP)
        self.connection.autocommit = True
        from core.celery import app
        self.app = app

    def apply_operational_migrations(self):
        versions = []
        for path in sorted((ROOT / 'BE_GO/migrations').glob('0*_*.up.sql')):
            self.run([self.psql, '-h', self.work, '-U', USER, '-d', DATABASE, '-X', '-q', '-v', 'ON_ERROR_STOP=1',
                      '-f', path], env=self.base_env)
            versions.append(int(path.name.split('_', 1)[0]))
        self.sql('CREATE TABLE public.assozeta_go_schema_migrations (version bigint NOT NULL PRIMARY KEY, dirty boolean NOT NULL)')
        self.sql('INSERT INTO public.assozeta_go_schema_migrations VALUES (%s, false)', (max(versions),))
        self.migration_versions = versions

    def install_effect_counter(self):
        self.sql('''CREATE TABLE handoff_test_effects (id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT clock_timestamp(),
          application text NOT NULL, relation text NOT NULL)''')
        self.sql('''CREATE FUNCTION handoff_test_count_effect() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN INSERT INTO handoff_test_effects(application, relation)
            VALUES (current_setting('application_name'), TG_TABLE_NAME); RETURN NULL; END $$''')
        self.sql('''CREATE TRIGGER handoff_test_reset_effect AFTER UPDATE ON communications_communicationconfiguration
          FOR EACH STATEMENT EXECUTE FUNCTION handoff_test_count_effect()''')

    def close(self):
        for process, stream, _ in self.children:
            if process.poll() is None:
                process.kill()
                with suppress(Exception):
                    process.wait(timeout=10)
            stream.close()
        with suppress(Exception):
            self.connection.close()
        with suppress(Exception):
            from django.db import connections
            connections.close_all()
        if self.redis_process:
            self.redis_process.terminate()
            with suppress(Exception):
                self.redis_process.wait(timeout=10)
        if self.pg_started:
            with suppress(Exception):
                self.run([self.bin / 'pg_ctl', '-D', self.work / 'pg', '-m', 'immediate', '-w', 'stop'], env=self.base_env)
        with suppress(Exception):
            shutil.copyfile(self.work / 'postgres.log', self.output / 'postgres.log')
        self.log.close()
        shutil.rmtree(self.work, ignore_errors=True)

    # -- SQL --------------------------------------------------------------------------------------
    def sql(self, statement, parameters=()):
        with self.connection.cursor() as cursor:
            cursor.execute(statement, parameters or None)
            return cursor.fetchall() if cursor.description else None

    def exists(self, regclass):
        return self.sql('SELECT to_regclass(%s) IS NOT NULL', (regclass,))[0][0]

    def require_table(self, table):
        check(self.exists('public.' + table), f'contract: public.{table} does not exist (migration 000006_job_handoff)')

    def columns(self, table):
        return {row[0] for row in self.sql("SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                                           'AND table_name=%s', (table,))}

    def authority(self):
        return self.sql('SELECT runtime, generation FROM public.assozeta_go_job_ownership WHERE singleton=1')[0]

    def commit_authority(self, operation_id, runtime, generation, target):
        return self.sql('SELECT outcome, runtime, generation FROM public.assozeta_commit_runtime_authority(%s, %s, %s, %s)',
                        (operation_id, runtime, generation, target))[0]

    def set_authority(self, runtime, generation):
        """Fixture only: the reset between scenarios (never a handoff)."""
        columns = self.columns('assozeta_go_job_ownership')
        assignments = ['runtime=%s', 'generation=%s', 'operation_id=NULL', 'committed_at=NULL']
        if 'admission' in columns:
            assignments.append("admission='open'")
        if 'admission_operation_id' in columns:
            assignments.append('admission_operation_id=NULL')
        self.sql(f'UPDATE public.assozeta_go_job_ownership SET {", ".join(assignments)} WHERE singleton=1',
                 (runtime, generation))

    def effects(self):
        return dict(self.sql('SELECT application, count(*) FROM handoff_test_effects GROUP BY application'))

    def effect_total(self):
        return self.sql('SELECT count(*) FROM handoff_test_effects')[0][0]

    def go_job(self, identifier):
        rows = self.sql('''SELECT state, attempts, lease_owner, ownership_generation, error_code, kind, created_at, available_at
          FROM public.assozeta_go_jobs WHERE id=%s''', (identifier,))
        return rows[0] if rows else None

    def task_result(self, task_id):
        rows = self.sql('SELECT status FROM django_celery_results_taskresult WHERE task_id=%s', (task_id,))
        return rows[0][0] if rows else None

    def work_row(self, task_id):
        self.require_table('assozeta_handoff_work')
        with self.connection.cursor() as cursor:
            cursor.execute('SELECT * FROM public.assozeta_handoff_work WHERE task_id=%s', (task_id,))
            row = cursor.fetchone()
            return dict(zip([c.name for c in cursor.description], row)) if row else None

    def attempts(self, task_id=None, consumer_id=None):
        self.require_table('assozeta_handoff_attempts')
        clauses, parameters = [], []
        if task_id:
            clauses.append('task_id=%s')
            parameters.append(task_id)
        if consumer_id:
            clauses.append('consumer_id=%s')
            parameters.append(consumer_id)
        where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with self.connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM public.assozeta_handoff_attempts {where} ORDER BY claimed_at', parameters)
            return [dict(zip([c.name for c in cursor.description], row)) for row in cursor.fetchall()]

    def consumers(self, runtime=None):
        self.require_table('assozeta_handoff_consumers')
        with self.connection.cursor() as cursor:
            cursor.execute('SELECT * FROM public.assozeta_handoff_consumers' + (' WHERE runtime=%s' if runtime else ''),
                           (runtime,) if runtime else None)
            return [dict(zip([c.name for c in cursor.description], row)) for row in cursor.fetchall()]

    def reset(self):
        """Between scenarios: fresh broker, ledger, jobs, results, effects; authority django/1, admission open."""
        for process, stream, _ in self.children:
            if process.poll() is None:
                process.kill()
                with suppress(Exception):
                    process.wait(timeout=10)
        self.redis.flushall()
        present = [table for table in HANDOFF_TABLES if self.exists('public.' + table)]
        self.sql('TRUNCATE ' + ', '.join(['public.assozeta_go_periodic_occurrences', 'public.assozeta_go_jobs',
                                          'django_celery_results_taskresult', 'handoff_test_effects',
                                          'communications_communicationconfiguration', 'instance_datarestore',
                                          *('public.' + t for t in present)]) + ' CASCADE')
        self.set_authority('django', 1)
        self.barrier.reset()

    def seed_balance(self):
        from communications.models import CommunicationConfiguration
        from application.models import User, SportAssociation
        owner, association = uuid.UUID(int=0xA3000001), uuid.UUID(int=0xA3000011)
        if not User.original_objects.filter(user_id=owner).exists():
            User.original_objects.bulk_create([User(user_id=owner, username='handoff-owner', role=1,
                                                    password='!private-fixture', email='owner@example.test')])
            SportAssociation.original_objects.bulk_create([SportAssociation(
                sport_association_id=association, user_id=owner, denomination='ASD Handoff', tax_code='00000000000')])
        CommunicationConfiguration.objects.bulk_create([CommunicationConfiguration(
            communication_configuration_id=uuid.UUID(int=0xA3000031), sport_association_id=association,
            email_sender_name='Handoff', email_encryption='NONE', daily_email_limit=1000, daily_email_balance=7)])
        self.sql('TRUNCATE handoff_test_effects')

    # -- Django producer and worker -------------------------------------------------------------
    def django_publish(self, task_id, **options):
        """The production Django producer path: Task.apply_async on the private broker.

        Returns ('returned', None) or ('raised', '<module>.<Class>')."""
        from application.tasks import reset_email_balance
        try:
            reset_email_balance.apply_async(task_id=task_id, **options)
        except Exception as exc:  # the admission result is the contract under test
            return 'raised', f'{type(exc).__module__}.{type(exc).__name__}'
        return 'returned', None

    def redis_copies(self, task_id):
        """Every Redis value that carries task_id (lists, the unacked hash), by key."""
        found = {}
        for key in self.redis.keys('*'):
            kind = self.redis.type(key).decode()
            name = key.decode('utf-8', 'backslashreplace')
            if kind == 'list':
                values = self.redis.lrange(key, 0, -1)
            elif kind == 'hash':
                values = list(self.redis.hgetall(key).values())
            else:
                continue
            hits = [value for value in values if task_id.encode() in value]
            if hits:
                found[name] = hits
        return found

    def django_worker(self, name='handoff'):
        log = self.output / f'django-worker-{len(self.children) + 1}.log'
        stream = log.open('w')
        env = dict(self.env, ASSOZETA_HANDOFF_APPLICATION=DJANGO_WORKER_APP)
        process = subprocess.Popen([sys.executable, '-m', 'TESTS.adapters.go.consumer_restore_worker', '-P', 'prefork', '-c', '1',
                                    '-Q', 'celery', '-n', f'{name}-{len(self.children)}@%h', '--without-heartbeat',
                                    '--without-gossip', '--without-mingle', '-l', 'WARNING'],
                                   cwd=ROOT / 'BE', env=env, stdout=stream, stderr=subprocess.STDOUT)
        self.children.append((process, stream, log))
        return process

    def django_ready(self, timeout=60):
        """Positive evidence that a Django worker's consumer is running: a remote-control ping reply."""
        def pong():
            try:
                return self.app.control.ping(timeout=0.5)
            except Exception:
                return None
        return wait_for(pong, 'Django worker startup (remote-control ping)', timeout=timeout)

    def go_env_file(self):
        path = self.work / 'go.env'
        if not path.exists():
            values = {'DBTYPE': 'POSTGRES', 'DBNAME': DATABASE, 'DBUSER': USER, 'DBPASSWORD': DB_PASSWORD,
                      'DBHOST': str(self.work), 'DBPORT': '5432', 'DEBUG': 'False', 'SECRET_KEY': secrets.token_hex(16),
                      'JWT_SECRET_KEY': 'fixture', 'JWT_PUBLIC_KEY': 'fixture', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': '',
                      'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': '', 'AWS_ACCESS_KEY_ID': 'fixture', 'AWS_SECRET_ACCESS_KEY': 'fixture',
                      'AWS_STORAGE_BUCKET_NAME': 'fixture', 'AWS_S3_REGION_NAME': 'us-east-1',
                      'AWS_S3_ENDPOINT_URL': 'http://127.0.0.1:9', 'AWS_LOCATION': 'private', 'REDIS_HOST': '127.0.0.1',
                      'REDIS_PORT': str(self.redis_port), 'REDIS_USERNAME': 'default', 'REDIS_PASSWORD': REDIS_PASSWORD,
                      'GO_TASK_QUEUE_TASKS': '', 'DATA_RESTORE_LOCK_PATH': str(self.work / 'contract-lifecycle.lock')}
            path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
            path.chmod(0o600)
        return path

    def go_worker(self):
        """cmd/worker (-tags handoff, race build) with test-speed intervals and the Phase 3 heartbeat flag."""
        log = self.output / f'go-worker-{len(self.children) + 1}.log'
        stream = log.open('w')
        env = dict(self.base_env, ASSOZETA_HANDOFF_BARRIER_DIR=str(self.barrier.directory))
        command = [str(self.work / 'worker'), '--env-file', str(self.go_env_file()), '--concurrency', '1',
                   '--poll-interval', '50ms', '--maintenance-interval', '50ms', '--lease', '5s', '--renew-interval', '500ms',
                   '--store-timeout', '2s', '--task-timeout', '20s', '--drain-timeout', '3s', '--handoff-heartbeat', '100ms']
        process = subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
        self.children.append((process, stream, log))
        wait_for(lambda: 'worker ready' in log.read_text() or process.poll() is not None, 'Go worker startup')
        if process.poll() is not None:
            text = log.read_text()
            check('invalid worker arguments' not in text,
                  'contract: cmd/worker refused --handoff-heartbeat (Phase 3 freeze heartbeat flag missing)')
            raise ContractFailure('Go worker exited during startup: ' + text[-800:])
        return process

    def stop(self, process, timeout=30):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait(timeout=10)
        for _, stream, log in self.children:
            stream.flush()
        for _, _, log in self.children:
            text = log.read_text() if log.exists() else ''
            check('WARNING: DATA RACE' not in text, 'Go race detector reported a data race: ' + log.name)
            for private in (REDIS_PASSWORD, DB_PASSWORD):
                check(private not in text, 'a worker log leaked private configuration: ' + log.name)
        return code

    def go_enqueue(self, job_id, available_at=None, kind=DB_TASK, key=None):
        """A Go durable producer (cmd/handoff-probe) calling the production repository.Jobs.Enqueue."""
        command = [str(self.work / 'handoff-probe'), 'enqueue', '--dsn',
                   f'host={self.work} user={USER} dbname={DATABASE} sslmode=disable', '--id', job_id, '--kind', kind,
                   '--key', key or f'probe:{job_id}']
        if available_at is not None:
            command += ['--available-at', available_at.astimezone(timezone.utc).isoformat()]
        completed = subprocess.run(command, env=self.base_env, capture_output=True, text=True, timeout=60)
        try:
            return json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError('handoff-probe produced no result: ' + completed.stdout + completed.stderr) from None

    # -- controller seams -------------------------------------------------------------------------
    def job_handoff(self):
        updater = str(ROOT / 'selfhost/updater')
        if updater not in sys.path:
            sys.path.insert(0, updater)
        try:
            import job_handoff
        except ImportError as exc:
            raise ContractFailure('contract: selfhost/updater/job_handoff.py is missing '
                                  f'(handoff_steps, PsqlStore, RespBroker): {exc}') from None
        for name in ('handoff_steps', 'PsqlStore', 'RespBroker'):
            check(hasattr(job_handoff, name), f'contract: job_handoff.{name} is missing')
        return job_handoff

    def psql_command(self):
        return [self.psql, '-h', str(self.work), '-U', USER, '-d', DATABASE, '-X', '-q', '-At', '-v', 'ON_ERROR_STOP=1']

    def handoff_steps(self, **options):
        module = self.job_handoff()
        store = module.PsqlStore(self.psql_command(), dict(self.base_env))
        broker = module.RespBroker('127.0.0.1', self.redis_port, username='default', password=REDIS_PASSWORD, db=0)
        options.setdefault('freeze_timeout', 30.0)
        options.setdefault('settle_timeout', 30.0)
        return module.handoff_steps(store, broker, **options)

    @staticmethod
    def record(operation_id, source, generation, target):
        return {'id': str(operation_id), 'previous_runtime': source, 'source_generation': generation, 'target': target}

    @staticmethod
    def now():
        return datetime.now(timezone.utc)
