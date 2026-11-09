"""relay_go_realtime --once: actual Django management command versus the Go command.

Private PostgreSQL with the current Django schema plus the Go-owned outbox
(BE_GO/migrations/000004_realtime_events.up.sql) and a private redis-server
with a password. Every authored case recreates the outbox with the same rows,
flushes Redis and seeds Channels group memberships, then runs
``manage.py relay_go_realtime --once`` as a separate process with the shipped
CHANNEL_LAYERS (core.settings: GoMirroredRedisChannelLayer on Redis DB 0),
records exit status, stdout, the outbox table, every Redis key (channel
message queues decoded from channels_redis' random prefix + msgpack, group
sets, TTLs) and the mirror envelopes received by a subscriber on
``assozeta:realtime:v1``; it then resets and runs the race-built Go binary
with the same arguments and environment, and requires the identical result.
Run-time values (delivered_at, message scores, TTLs) are checked against the
run window before they are normalized. Django must first meet the authored
expectation (stdout counts and per-row outcomes).
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_relay_go_realtime'
REDIS_PASSWORD = 'private-relay-redis-canary'
OUTBOX_DDL = (ROOT / 'BE_GO/migrations/000004_realtime_events.up.sql').read_text()
TABLE = 'public.assozeta_go_realtime_events'
A, B, C = UUID(int=0xA1), UUID(int=0xB2), UUID(int=0xC3)
MIRROR = 'assozeta:realtime:v1'
DJANGO = f'''
import sys
sys.path[:0] = [{str(ROOT / 'BE')!r}, {str(ROOT)!r}]
from unittest.mock import patch
with patch('environ.Env.read_env'):
    import django
    django.setup()
import core.settings as shipped
from django.test import override_settings
from django.core.management import execute_from_command_line
with override_settings(CHANNEL_LAYERS=shipped.CHANNEL_LAYERS):
    execute_from_command_line(['manage.py', 'relay_go_realtime', *sys.argv[1:]])
'''


def event(kind='staff_board_changed', target=A, version=1, attempts=0, delivered=False, failed=None):
    return {'version': version, 'kind': kind, 'target': target, 'attempts': attempts, 'delivered': delivered, 'failed': failed}


BASE = [
    event(),                                   # 1 pending, group A has two channels
    event(delivered=True, attempts=1),         # 2 already delivered
    event(failed='unsupported_event', attempts=1),  # 3 already failed
    event(kind='future_event'),                # 4 unknown kind
    event(target=B),                           # 5 group B has one channel
    event(target=C, attempts=29),              # 6 last attempt, group C has only an expired member
    event(target=A, attempts=28),              # 7
]
SENT = 'sent'


def counts(delivered=0, retry=0, failed=0):
    return f"{{'delivered': {delivered}, 'retry': {retry}, 'failed': {failed}}}\n"


# (name, rows, setup, arguments, environment overrides, authored stdout, authored {row: (attempts, delivered, failed)},
#  authored mirror groups in order). Rows not listed keep their seeded state.
CASES = [
    ('mixed outcomes', BASE, None, ['--once'], {}, counts(4, 0, 1),
     {1: (1, True, None), 4: (1, False, 'unsupported_event'), 5: (1, True, None), 6: (30, True, None), 7: (29, True, None)},
     [f'staff_board_{A}', f'staff_board_{B}', f'staff_board_{C}', f'staff_board_{A}']),
    ('poll seconds ignored with once', BASE[:2], None, ['--poll-seconds=0.5', '--once'], {}, counts(1),
     {1: (1, True, None)}, [f'staff_board_{A}']),
    ('nothing pending', BASE[1:3], None, ['--once'], {}, counts(), {}, []),
    ('missing outbox table', BASE, 'drop', ['--once'], {}, counts(), None, []),
    ('envelope version other than 1', [event(version=2), event()], 'version', ['--once'], {}, counts(1, 0, 1),
     {1: (1, False, 'unsupported_event'), 2: (1, True, None)}, [f'staff_board_{A}']),
    ('row locked by another transaction is skipped', BASE[:1] + [event(target=B)], 'lock', ['--once'], {}, counts(1),
     {2: (1, True, None)}, [f'staff_board_{B}']),
    ('limit of 100 per run', [event() for _ in range(105)], None, ['--once'], {}, counts(100),
     {i: (1, True, None) for i in range(1, 101)}, [f'staff_board_{A}'] * 100),
    ('channel layer unreachable: retry, then exhausted', BASE, None, ['--once'], {'REDIS_PORT': 'closed'}, counts(0, 3, 2),
     {1: (1, False, None), 4: (1, False, 'unsupported_event'), 5: (1, False, None), 6: (30, False, 'delivery_exhausted'),
      7: (29, False, None)}, None),
    ('channel layer refuses the password', BASE[:1] + [BASE[5]], None, ['--once'], {'REDIS_PASSWORD': 'wrong-password'},
     counts(0, 1, 1), {1: (1, False, None), 2: (30, False, 'delivery_exhausted')}, []),
]


class Fixture:
    def __init__(self, redis_port):
        import redis
        self.redis = redis.Redis(host='127.0.0.1', port=redis_port, password=REDIS_PASSWORD, db=0)
        self.seeded_score = None
        self.rows = []

    def reset(self, rows, setup):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS {TABLE}')
            cursor.execute(OUTBOX_DDL)
            if setup == 'version':
                cursor.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT assozeta_go_realtime_events_envelope_version_check')
            for row in rows:
                cursor.execute(
                    f'INSERT INTO {TABLE} (envelope_version, kind, target_id, created_at, attempts, delivered_at, failed_code) '
                    "VALUES (%s, %s, %s, '2026-01-02T09:00:00Z', %s, %s, %s)",
                    [row['version'], row['kind'], row['target'], row['attempts'],
                     '2026-01-02T10:00:00Z' if row['delivered'] else None, row['failed']])
            if setup == 'drop':
                cursor.execute(f'DROP TABLE {TABLE}')
        self.rows = rows
        self.redis.flushall()
        now = int(time.time())
        self.seeded_score = now - 10
        # Channels group sets: name -> score. C holds only a member older than group_expiry.
        self.redis.zadd(f'asgi:group:staff_board_{A}', {'specific.relayA!one': self.seeded_score, 'plain.relay': self.seeded_score})
        self.redis.zadd(f'asgi:group:staff_board_{B}', {'specific.relayB!two': self.seeded_score})
        self.stale_score = now - 90000
        self.redis.zadd(f'asgi:group:staff_board_{C}', {'stale.relay': self.stale_score})

    def outbox(self, since, until):
        from django.db import connection
        from datetime import datetime, timezone
        with connection.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s) IS NOT NULL', [TABLE])
            if not cursor.fetchone()[0]:
                return None
            cursor.execute(f'SELECT id, envelope_version, kind, target_id, created_at, attempts, delivered_at, failed_code '
                           f'FROM {TABLE} ORDER BY id')
            rows = []
            for identifier, version, kind, target, created, attempts, delivered, failed in cursor.fetchall():
                if delivered is not None and delivered != datetime(2026, 1, 2, 10, tzinfo=timezone.utc):
                    assert since <= delivered.timestamp() <= until, 'delivered_at inside the run'
                    delivered = 'run'
                rows.append([identifier, version, kind, str(target), created.isoformat(), attempts,
                             delivered.isoformat() if hasattr(delivered, 'isoformat') else delivered, failed])
            return rows

    def redis_state(self, since, until):
        import msgpack
        state = {}
        for key in sorted(self.redis.keys('*')):
            name = key.decode()
            kind = self.redis.type(key).decode()
            ttl = self.redis.ttl(key)
            assert kind == 'zset', f'unexpected key {name}'
            members = []
            for member, score in self.redis.zrange(key, 0, -1, withscores=True):
                if name.startswith('asgi:group:'):
                    members.append([member.decode(), {self.seeded_score: 'seeded', self.stale_score: 'stale'}.get(score, score)])
                    continue
                assert since - 1 <= score <= until + 1, 'message score is time.time() of the send'
                assert len(member) > 12, 'random prefix and payload'
                members.append([msgpack.unpackb(member[12:], raw=False), 'run'])
            if name.startswith('asgi:group:'):
                ttl = 'unset' if ttl == -1 else ttl
            else:
                assert 0 < ttl <= 60, 'channel expiry'
                ttl = 'expiry'
            state[name] = {'ttl': ttl, 'members': members}
        return state

    def authored_rows(self, changed):
        rows = []
        for index, row in enumerate(self.rows, start=1):
            attempts, delivered, failed = changed.get(index, (row['attempts'], row['delivered'], row['failed']))
            if delivered is True and not row['delivered']:
                delivered = 'run'
            elif delivered:
                delivered = '2026-01-02T10:00:00+00:00'
            else:
                delivered = None
            rows.append([index, row['version'], row['kind'], str(row['target']), '2026-01-02T09:00:00+00:00',
                         attempts, delivered, failed])
        return rows


class Mirror:
    """Collects the mirror envelopes published while a command runs."""

    def __init__(self, port):
        import redis
        self.client = redis.Redis(host='127.0.0.1', port=port, password=REDIS_PASSWORD, db=0)
        self.pubsub = self.client.pubsub()
        self.pubsub.subscribe(MIRROR)
        assert self.pubsub.get_message(timeout=5)['type'] == 'subscribe'
        self.messages = []

    def collect(self):
        deadline = time.time() + 0.5
        while time.time() < deadline:
            message = self.pubsub.get_message(timeout=0.1)
            if message and message['type'] == 'message':
                self.messages.append(message['data'].decode())
                deadline = time.time() + 0.5
        self.pubsub.close()
        self.client.close()
        return self.messages


def run_case(fixture, executable, environment, redis_port, closed_port, case, output):
    name, rows, setup, arguments, overrides, stdout, changed, mirror = case
    results = {}
    for runtime in ('django', 'go'):
        fixture.reset(rows, setup)
        if runtime == 'go' and executable is None:
            return {'case': name, 'django': 'pass', 'go': 'missing command'}
        env = dict(environment, REDIS_PORT=str(redis_port))
        env.update({k: (str(closed_port) if v == 'closed' else v) for k, v in overrides.items()})
        command = [sys.executable, '-I', '-c', DJANGO, *arguments] if runtime == 'django' else [str(executable), *arguments]
        locker = None
        if setup == 'lock':
            import psycopg2
            locker = psycopg2.connect(dbname=DATABASE, user='contracts', host=environment['DBHOST'], port=5432)
            locker.cursor().execute(f'SELECT id FROM {TABLE} WHERE id = 1 FOR UPDATE')
        listener = Mirror(redis_port)
        since = time.time()
        try:
            completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=180,
                                       start_new_session=True, cwd=ROOT / 'BE')
        finally:
            if locker is not None:
                locker.rollback()
                locker.close()
        until = time.time()
        published = listener.collect()
        results[runtime] = {'status': completed.returncode, 'stdout': completed.stdout,
                            'outbox': fixture.outbox(since, until), 'redis': fixture.redis_state(since, until),
                            'mirror': [json.loads(text) for text in published], 'mirror_text': published}
        if runtime == 'django':
            got = results['django']
            assert got['status'] == 0, f'{name}: Django exit {got["status"]}: {completed.stderr[-2000:]}'
            assert got['stdout'] == stdout, f'{name}: Django stdout {got["stdout"]!r}'
            if changed is None:
                assert got['outbox'] is None, f'{name}: outbox expected absent'
            else:
                assert got['outbox'] == fixture.authored_rows(changed), f'{name}: Django outbox differs from the authored rows'
            groups = [message['group'] for message in got['mirror']]
            assert groups == (mirror or []), f'{name}: Django mirror groups {groups}'
            assert all(message == {'version': 1, 'group': message['group'], 'message': {'type': 'staff_board_changed'}}
                       for message in got['mirror']), f'{name}: mirror envelope'
    if results['django'] != results['go']:
        (output / 'difference.json').write_text(json.dumps({'case': name, **results}, indent=1, default=str) + '\n')
    assert results['django'] == results['go'], f'{name}: Go differs from the Django command'
    print(f'PASS: {name} ({stdout.strip()})', flush=True)
    return {'case': name, 'stdout': stdout, 'mirror_messages': len(results['django']['mirror']),
            'redis_keys': sorted(results['django']['redis']), 'django': 'pass', 'go': 'pass'}


def free_port():
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        return reservation.getsockname()[1]


def main():
    require_production_python()
    label = os.environ.get('RELAY_LABEL', 'run')
    output = ROOT / f'quality-reports/go-migration/relay-go-realtime/{label}'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    redis_server = shutil.which('redis-server')
    if not Path(postgres).is_file() or not shutil.which('go') or not redis_server:
        raise RuntimeError('Private PostgreSQL, redis-server and Go are required')
    binaries = Path(postgres).parent
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    base.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-relay-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory)
        attempted = False
        redis_process = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=base, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            executable = work / 'relay-go-realtime'
            built = subprocess.run(['go', 'build', '-race', '-o', str(executable), './cmd/relay-go-realtime'],
                                   cwd=ROOT / 'BE_GO', env=base, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            if built.returncode:
                executable = None  # RED: the Django cases still run against their authored expectations.
            redis_port, closed_port = free_port(), free_port()
            redis_process = subprocess.Popen([redis_server, '--bind', '127.0.0.1', '--port', str(redis_port), '--save', '',
                                              '--appendonly', 'no', '--requirepass', REDIS_PASSWORD, '--dir', str(work)],
                                             stdout=log, stderr=subprocess.STDOUT)
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            environment = dict(os.environ)
            environment.update(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
                               DBPASSWORD='private-relay-password-canary', SECRET_KEY='private-relay-secret-canary',
                               DEBUG='False', REDIS_HOST='127.0.0.1', REDIS_PASSWORD=REDIS_PASSWORD, REDIS_USERNAME='default',
                               REDIS_SSLAUTH='', DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'), DJANGO_COLORS='nocolor')
            fixture = Fixture(redis_port)
            deadline = time.time() + 10
            while True:
                try:
                    fixture.redis.ping()
                    break
                except Exception:
                    assert time.time() < deadline, 'redis did not start'
                    time.sleep(0.05)
            result['cases'] = []
            for case in CASES:
                try:
                    result['cases'].append(run_case(fixture, executable, environment, redis_port, closed_port, case, output))
                except AssertionError as failure:
                    result['failure'] = {'case': case[0], 'error': str(failure)}
                    raise
            result['django_passed'] = sum(case['django'] == 'pass' for case in result['cases'])
            result['go_passed'] = sum(case['go'] == 'pass' for case in result['cases'])
            print(json.dumps({'django': f"{result['django_passed']}/{len(CASES)}", 'go': f"{result['go_passed']}/{len(CASES)}"}))
            assert result['go_passed'] == len(CASES), 'Go command missing or failing'
        finally:
            try:
                from django.db import connections
                connections.close_all()
            finally:
                if attempted:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                if redis_process is not None:
                    redis_process.terminate()
                    redis_process.wait(timeout=10)
                report.write_text(json.dumps(result, indent=2) + '\n')
    paths = ['TESTS/adapters/go/relay_go_realtime_command.py', 'BE/notifications/management/commands/relay_go_realtime.py',
             'BE/notifications/go_realtime.py', 'BE/notifications/realtime_layer.py', 'BE/communications/staff_board.py',
             'BE_GO/migrations/000004_realtime_events.up.sql', 'BE_GO/cmd/relay-go-realtime/main.go',
             'BE_GO/internal/service/relay_go_realtime.go', 'BE_GO/internal/repository/relay_go_realtime.go',
             'BE_GO/internal/domain/relay_go_realtime.go', 'BE_GO/internal/channels/channels.go',
             'BE_GO/internal/channels/python.go']
    result.update(success=True, case_count=len(result['cases']), race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'success': True, 'case_count': result['case_count']}))


if __name__ == '__main__':
    main()
