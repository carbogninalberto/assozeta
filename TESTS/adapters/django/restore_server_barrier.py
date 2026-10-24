"""Actual Go/Django server-drain ordering on a disposable transaction pooler.

Business rows are synthetic transaction sentinels. Actual restore execution is
entered, but operation/export collaborators are observed substitutes so no graph
replacement or external storage runs. Existing restore tests cover graph parity.
"""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg2
from psycopg2.extensions import parse_dsn

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.private_pooler import private_pooler

KEY = (1095981639, 1381192786)
TRIGGER_GATE = 77123456789
OWNER = '00000000-0000-4000-8000-000000000001'


def until(predicate, description, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.025)
    raise AssertionError('Timed out: ' + description)


class SnapshotTaken(BaseException):
    pass


def configure_django(runtime_dsn, work):
    import environ
    keep = {key: value for key, value in os.environ.items()
            if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    os.environ.clear()
    os.environ.update(keep, LC_ALL='C', LANG='C')
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    params = parse_dsn(runtime_dsn)
    os.environ.update(DJANGO_SETTINGS_MODULE='core.settings', DEBUG='False',
                      JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture',
                      DBTYPE='POSTGRES', DBHOST=params['host'], DBPORT=params.get('port', '5432'),
                      DBNAME=params['dbname'], DBUSER=params['user'], DBPASSWORD=params.get('password', ''),
                      USE_PGBOUNCER='False', STORE_ON_DISK='True', DATA_RESTORE_LOCK_PATH=str(work / 'lifecycle.lock'))
    # The endpoint itself is PgBouncer; keeping USE_PGBOUNCER=False avoids reading
    # any ambient alternative endpoint. Both runtime participants use one DSN.
    sys.path.insert(0, str(ROOT / 'BE'))
    import django
    with patch('environ.Env.read_env'):
        django.setup()


@contextlib.contextmanager
def observed_execute(read_snapshot):
    from django.conf import settings
    from django.db import connections
    from instance.restore.execution import execute
    operation = SimpleNamespace(id=uuid4(), state='queued', attempts=0, association_id=uuid4(),
                                owner=object(), version=settings.RUNNING_VERSION, upload_path='private-fixture',
                                sha256='fixture-sha', allow_missing_media=False, refresh_from_db=lambda: None)
    config = SimpleNamespace(primary_association_id=operation.association_id)
    events = []
    def get_operation(*args, **kwargs):
        events.append('operation_read')
        return operation
    def save_operation(op, **changes):
        events.append('state_write')
        for name, value in changes.items():
            setattr(op, name, value)
    def export():
        events.append('recovery_export')
        events.append(read_snapshot())
        raise SnapshotTaken()
    @contextlib.contextmanager
    def local_copy(_):
        yield 'unused-fixture-path'
    with contextlib.ExitStack() as stack:
        targets = {
            'DataRestore.objects.get': get_operation,
            'InstanceConfiguration.objects.select_related': MagicMock(return_value=SimpleNamespace(get=lambda: config)),
            'is_instance_administrator': lambda *args: True,
            'require_single': lambda *args: None,
            'save_operation': save_operation,
            'RestoreProgress': MagicMock(),
            'local_copy': local_copy,
            'digest': lambda *args: 'fixture-sha',
            'Archive': MagicMock(return_value=SimpleNamespace(validate=lambda: SimpleNamespace(missing_media=False))),
            'RecoveryExport': MagicMock(return_value=SimpleNamespace(export=export)),
            'replace': MagicMock(side_effect=AssertionError('replacement must not run in this ordering fixture')),
        }
        for suffix, replacement in targets.items():
            stack.enter_context(patch('instance.restore.execution.' + suffix, replacement))
        def run(errors):
            try:
                execute(operation.id)
            except SnapshotTaken:
                pass
            except BaseException as exc:
                errors.append(exc)
            finally:
                connections.close_all()
        yield run, events, operation


def build_writer(binary):
    # Go internal-package imports require the temporary command to
    # live beneath BE_GO; its source and binary are always removed.
    with tempfile.TemporaryDirectory(prefix='.restore-drain-probe-', dir=ROOT / 'BE_GO') as source_dir:
        source = Path(source_dir) / 'main.go'
        shutil.copyfile(ROOT / 'TESTS/adapters/django/testdata/restore_server_barrier.go', source)
        subprocess.run(['go', 'build', '-race', '-o', str(binary), str(source)],
                       cwd=ROOT / 'BE_GO', check=True, timeout=120, capture_output=True)


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/restore-server-barrier'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': __doc__.strip()}
    (output / 'results.json').write_text(json.dumps(result) + '\n')
    cases = []
    with tempfile.TemporaryDirectory(prefix='az-restore-boundary-', dir='/tmp') as directory:
        work = Path(directory)
        with private_pooler(output, 'assozeta_contract_restore_barrier') as fixture:
            configure_django(fixture.pooled_dsn, work)
            from django.db import connection, connections
            from instance.restore.locking import Busy
            from instance.restore.server_barrier import drain_go_transactions
            admin = psycopg2.connect(fixture.direct_dsn)
            admin.autocommit = True
            gate = psycopg2.connect(fixture.direct_dsn)
            process = None
            worker = None
            def sql(statement, parameters=()):
                with admin.cursor() as cursor:
                    cursor.execute(statement, parameters)
                    return cursor.fetchall() if cursor.description else None
            def gate_sql(statement, parameters=()):
                with gate.cursor() as cursor:
                    cursor.execute(statement, parameters)
            def snapshot():
                with connection.cursor() as cursor:
                    cursor.execute('SELECT tables_settings FROM bakney_user WHERE user_id=%s', [OWNER])
                    value = cursor.fetchone()[0]
                    return json.loads(value) if isinstance(value, str) else value
            try:
                sql('CREATE TABLE bakney_user(user_id uuid PRIMARY KEY, first_name text, last_name text, deleted boolean, tables_settings jsonb)')
                sql('INSERT INTO bakney_user VALUES(%s,%s,%s,false,%s)', (OWNER, 'Fixture', 'Owner', '{}'))
                # Ordinary deferred trigger: cancellation is not caught or
                # transformed by SQL. Only the separate transport fails.
                sql(f'''CREATE FUNCTION pause_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN PERFORM pg_advisory_xact_lock({TRIGGER_GATE}); RETURN NEW; END $$''')
                sql('CREATE CONSTRAINT TRIGGER pause_commit AFTER UPDATE ON bakney_user DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pause_commit()')
                # Real bounded timeout before any operation/state/export access.
                gate_sql('SELECT pg_advisory_xact_lock_shared(%s,%s)', KEY)
                try:
                    with observed_execute(snapshot) as (run, events, operation):
                        errors = []
                        started = time.monotonic()
                        run(errors)
                        elapsed = time.monotonic() - started
                        assert len(errors) == 1 and isinstance(errors[0], Busy), {
                            'errors': [type(error).__name__ for error in errors],
                            'events': events, 'attempts': operation.attempts, 'elapsed_seconds': elapsed}
                        assert not events and operation.attempts == 0
                        assert 4 <= elapsed <= 12, elapsed
                finally:
                    gate.commit()
                cases.append('actual restore timeout is Busy before operation state or export access')
                # The timeout is transaction-local and the pooled session remains usable.
                with connection.cursor() as cursor:
                    cursor.execute('SHOW statement_timeout')
                    assert cursor.fetchone()[0] == '0'
                drain_go_transactions()
                cases.append('retry succeeds and local timeout does not leak to pooled transactions')
                binary = work / 'writer'
                build_writer(binary)
                gate_sql('SELECT pg_advisory_xact_lock(%s)', [TRIGGER_GATE])
                environment = {key: value for key, value in os.environ.items()
                               if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
                environment.update(ASSOZETA_DRAIN_WRITER_DSN=fixture.pooled_dsn,
                                   ASSOZETA_DRAIN_LOCK_PATH=str(work / 'lifecycle.lock'))
                with (output / 'writer-stderr.log').open('w') as errors_log:
                    process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                               stderr=errors_log, text=True, env=environment)
                    messages = queue.Queue()
                    reader = threading.Thread(target=lambda: [messages.put(json.loads(line)) for line in process.stdout], daemon=True)
                    reader.start()
                    def event(name):
                        assert messages.get(timeout=12) == {'event': name}
                    event('prepared')
                    process.stdin.write('commit\n'); process.stdin.flush()
                    event('commit_started')
                    until(lambda: sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='az-restore-fence-writer' AND wait_event='advisory'")[0][0] == 1,
                          'ordinary deferred COMMIT waiting on the server')
                    process.stdin.write('cancel\n'); process.stdin.flush()
                    event('local_cleanup')
                    process.wait(timeout=12)
                    assert process.returncode == 0
                    with (work / 'lifecycle.lock').open('a') as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    assert sql('SELECT tables_settings FROM bakney_user WHERE user_id=%s', [OWNER])[0][0] == {}
                    with observed_execute(snapshot) as (run, events, operation):
                        errors = []
                        worker = threading.Thread(target=run, args=(errors,))
                        worker.start()
                        until(lambda: sql("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=%s AND objid=%s AND NOT granted", KEY)[0][0] >= 1,
                              'Django exclusive server fence')
                        assert worker.is_alive() and not events and operation.attempts == 0
                        gate.commit()
                        worker.join(timeout=12)
                        assert not worker.is_alive() and not errors, errors
                        assert events == ['operation_read', 'state_write', 'recovery_export', {'late_commit': True}], events
                    cases.append('actual Go late commit drains before Django operation access and recovery snapshot')
                    reader.join(timeout=2)
                result.update(cases=cases, case_count=len(cases), pooler=fixture.metadata)
            finally:
                # Unblock the real server before joining any pending restore or
                # process, even if an assertion/log operation failed.
                try:
                    gate.rollback()
                finally:
                    if process is not None and process.poll() is None:
                        process.kill(); process.wait(timeout=10)
                    if worker is not None:
                        worker.join(timeout=12)
                        if worker.is_alive():
                            raise RuntimeError('private Django restore thread did not terminate')
                    connections.close_all()
                    gate.close()
                    admin.close()
    assert len(cases) == 3
    result.update(success=True, private_fixture_removed=True)
    (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
