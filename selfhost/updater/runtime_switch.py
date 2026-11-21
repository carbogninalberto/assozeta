"""Serialize instance runtime changes and reconcile them against the PostgreSQL owner."""
from collections import namedtuple
from contextlib import ExitStack, contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

from common import compose_command, compose_environment, read_env
from journal import Conflict, Journal, now

RUNTIMES = ('django', 'go')
OPERATOR_ACTOR = '00000000-0000-4000-8000-000000000001'
RECOVERY = './bin/assozeta runtime-recover'
Authority = namedtuple('Authority', 'runtime generation operation_id')
CommitResult = namedtuple('CommitResult', 'outcome authority')


class AuthorityUnavailable(Exception):
    pass


class SwitchUnavailable(Exception):
    pass


class PsqlAuthority:
    def __init__(self, command, environment=None):
        self.command = command
        self.environment = environment

    def _query(self, sql, variables=()):
        try:
            output = subprocess.run([*self.command, *variables], input=sql, text=True, check=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, env=self.environment)
            fields = output.stdout.strip().split('|')
            runtime, generation, operation_id = fields[-3:]
            if runtime not in RUNTIMES or int(generation) < 1:
                raise ValueError()
            return fields[:-3], Authority(runtime, int(generation), str(UUID(operation_id)) if operation_id else None)
        except (ValueError, OSError, subprocess.SubprocessError):
            raise AuthorityUnavailable() from None

    def read(self):
        _, row = self._query('SELECT runtime, generation, operation_id FROM public.assozeta_go_job_ownership WHERE singleton=1;')
        return row

    def commit(self, operation_id, expected_runtime, expected_generation, target):
        fields, row = self._query(
            "SELECT * FROM public.assozeta_commit_runtime_authority(:'op', :'source', :'generation', :'target');",
            ['-v', f'op={operation_id}', '-v', f'source={expected_runtime}', '-v', f'generation={expected_generation}',
             '-v', f'target={target}'])
        if len(fields) != 1 or fields[0] not in ('committed', 'already_committed', 'conflict'):
            raise AuthorityUnavailable()
        return CommitResult(fields[0], row)


def compose_authority(root, env_file):
    values = read_env(env_file)
    command = compose_command(root, env_file) + ['exec', '-T', 'postgres', 'psql', '-X', '-qAt',
                                                  '-U', values.get('DBUSER', 'assozeta'),
                                                  '-d', values.get('DBNAME', 'assozeta'), '-v', 'ON_ERROR_STOP=1']
    return PsqlAuthority(command, compose_environment(root, env_file))


def read_authority(root, env_file):
    return compose_authority(root, env_file).read()


def operation_view(record):
    if record is None:
        return None
    if record.get('kind') != 'runtime_switch':
        return {'id': record['id'], 'kind': record.get('kind', 'update'), 'status': record['status'], 'stage': record['stage']}
    keys = ('id', 'kind', 'actor_id', 'previous_runtime', 'expected_generation', 'status', 'stage', 'outcome',
            'counts', 'committed_generation', 'error', 'error_code', 'recovery', 'created_at', 'updated_at')
    return {**{key: record.get(key) for key in keys}, 'requested_runtime': record['target']}


def submit(root, env_file, journal, authority, body, claim=False):
    invalid = (400, {'code': 'invalid_request', 'error': 'Richiesta non valida.'})
    if not isinstance(body, dict) or set(body) != {'request_id', 'actor_id', 'target', 'expected_generation'}:
        return invalid
    if body['target'] not in RUNTIMES or type(body['expected_generation']) is not int or body['expected_generation'] < 1:
        return invalid
    try:
        body = {**body, 'actor_id': str(UUID(body['actor_id'])), 'request_id': str(UUID(body['request_id']))}
    except (AttributeError, TypeError, ValueError):
        return invalid
    try:
        with journal.connect() as db:
            # A slow PostgreSQL read must leave the runner free to finish active
            # work. If the journal changed during the read, refresh the authority
            # before admitting a no-op or recording the source generation.
            authority = authority or compose_authority(root, env_file)
            while True:
                revision = db.execute('PRAGMA data_version').fetchone()
                row = authority.read()
                db.execute('BEGIN IMMEDIATE')
                if db.execute('PRAGMA data_version').fetchone() == revision:
                    break
                db.rollback()
            noop = row.runtime == body['target']
            request = {**body, 'kind': 'runtime_switch', 'source_generation': row.generation, 'previous_runtime': row.runtime,
                       'outcome': 'noop' if noop else None, 'committed_generation': None, 'error_code': None,
                       'failed_stage': None, 'counts': {'pending': 0, 'transferred': 0, 'blocked': 0}}
            pending = (Path(root) / '.updater/pending-distribution').exists()
            record, replayed = journal.create_in(
                db, request, blocked_reason='La transazione precedente richiede un ripristino.' if pending else None,
                status='succeeded' if noop else ('running' if claim else 'queued'), stage='completed' if noop else 'queued')
            if not replayed:
                log_stage(root, record)
    except (AuthorityUnavailable, OSError):
        return 503, {'code': 'authority_unavailable', 'error': 'Stato del runtime non disponibile.'}
    except Conflict as exc:
        view = operation_view(exc.record)
        active = view if view and view['status'] in ('queued', 'running', 'recovery_required') else None
        messages = {'idempotency_conflict': 'Identificativo già usato.',
                    'operation_in_progress': 'Operazione in corso.'}
        payload = {'code': exc.code, 'error': messages.get(exc.code, str(exc)), 'authority': row._asdict(), 'active': active}
        if exc.code == 'idempotency_conflict':
            payload['operation'] = view
            if not active:
                blocking = journal.records('queued', 'running', 'recovery_required')
                payload['active'] = operation_view(next(iter(blocking), None))
        return 409, payload
    return (200 if record['outcome'] == 'noop' else 202), {
        'operation': operation_view(record), 'authority': row._asdict(), 'replayed': replayed}


def status(root, env_file, journal, authority):
    error = None
    try:
        row = (authority or compose_authority(root, env_file)).read()
    except (AuthorityUnavailable, OSError):
        row = Authority(None, None, None)
        error = 'authority_unavailable'
    records = journal.records()
    operation = next((r for r in records if r.get('kind') == 'runtime_switch'), None)
    active = next((r for r in records if r['status'] in ('queued', 'running', 'recovery_required')), None)
    return {'protocol': 1, 'effective_runtime': row.runtime, 'generation': row.generation,
            'last_operation_id': row.operation_id, 'operation': operation_view(operation), 'active': operation_view(active),
            'requested_runtime': active.get('target') if active and active.get('kind') == 'runtime_switch' else None,
            'authority_error': error, 'recovery_command': RECOVERY}


PRE_COMMIT = ('preflight', 'close_admission', 'settle', 'stage_backlog')
POST_COMMIT = ('activate', 'verify')
STEP_STAGES = PRE_COMMIT + POST_COMMIT
BARRIER_POINTS = (*('stage:' + stage for stage in PRE_COMMIT), 'stage:commit', 'authority:committed',
                  'stage:activate', 'stage:verify', 'outcome:succeeded')


def default_steps(root, env_file):
    def unavailable(context):
        raise SwitchUnavailable()

    def noop(context):
        pass

    return {**dict.fromkeys(STEP_STAGES, noop), 'preflight': unavailable}


@contextmanager
def lifecycle_locks(root, caller_holds_locks, *, caller_holds_directory=False):
    root = Path(root)
    with ExitStack() as stack:
        if not caller_holds_locks:
            legacy = root / '.lifecycle.lock'
            for path in (root / '.lifecycle.flock', root / '.operations/lifecycle.lock'):
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = stack.enter_context(path.open('a'))
                if path.name == 'lifecycle.lock':
                    path.chmod(0o666)
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not caller_holds_directory and legacy.exists() and not (legacy / 'flock-managed').exists():
                raise BlockingIOError()
        yield


def log_stage(root, record):
    log = Path(root) / '.updater/logs' / f"{UUID(record['id'])}.log"
    entry = {key: record[key] for key in ('actor_id', 'previous_runtime', 'stage', 'counts', 'outcome')}
    entry.update(at=now(), operation_id=record['id'], requested_runtime=record['target'])
    try:
        log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(log, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600), 'a') as output:
            print(json.dumps(entry), file=output)
    except OSError:
        return False
    return True


ERRORS = {
    'switch_unavailable': 'Questa versione non supporta il cambio di runtime.',
    'authority_unavailable': 'Stato del runtime non disponibile.',
    'stale_generation': 'La generazione richiesta non è più attuale.',
    'authority_diverged': 'Il runtime corrente non corrisponde allo stato registrato.',
    'authority_unknown': 'Impossibile verificare il cambio di runtime.',
    'step_failed': 'Cambio di runtime non riuscito.',
    'post_commit_failed': 'Il nuovo runtime richiede una verifica.',
    'lifecycle_busy': 'Un’altra operazione è in corso.',
}


def fail(root, journal, record, code, stage, **changes):
    recoverable = code in ('authority_unknown', 'post_commit_failed')
    status = 'recovery_required' if recoverable else 'failed'
    failed = journal.update(record['id'], status=status, stage=status, outcome='failed', failed_stage=stage,
                            error=ERRORS[code], error_code=code, recovery=RECOVERY if recoverable else None, **changes)
    if failed:
        log_stage(root, failed)
    return failed


def advance(root, env_file, journal, authority, record, steps, barrier, recovering=False):
    steps = default_steps(root, env_file) if steps is None else steps
    operation_id = record['id']
    source = record['previous_runtime'], record['source_generation']
    destination = Authority(record['target'], source[1] + 1, operation_id)
    failed_stage = record.get('failed_stage') or record['stage']
    try:
        authority = authority or compose_authority(root, env_file)
        row = authority.read()
    except (AuthorityUnavailable, OSError):
        code = 'authority_unknown' if recovering else 'authority_unavailable'
        if recovering and record['status'] == 'recovery_required' and record.get('error_code') == code:
            return record
        return fail(root, journal, record, code, failed_stage)

    committed = row == destination
    if not committed and (row.runtime, row.generation) != source:
        return fail(root, journal, record, 'authority_diverged' if recovering else 'stale_generation', failed_stage)

    stages = POST_COMMIT if committed else PRE_COMMIT + ('commit',) + POST_COMMIT
    for stage in stages:
        record = journal.update(operation_id, status='running', stage=stage, committed_generation=row.generation if committed else None,
                                outcome=None, failed_stage=None, error=None, error_code=None, recovery=None)
        if not log_stage(root, record):
            return fail(root, journal, record, 'post_commit_failed' if committed else 'step_failed', stage)
        if barrier:
            barrier('stage:' + stage)
        if stage == 'commit':
            try:
                result = authority.commit(operation_id, *source, record['target'])
            except (AuthorityUnavailable, OSError):
                return fail(root, journal, record, 'authority_unknown', stage)
            if result.outcome == 'conflict' or result.authority != destination:
                return fail(root, journal, record, 'authority_diverged' if recovering else 'stale_generation', stage)
            row, committed = result.authority, True
            if barrier:
                barrier('authority:committed')
        else:
            try:
                steps[stage](record)
            except Exception as exc:
                code = 'post_commit_failed' if committed else 'step_failed'
                if stage == 'preflight' and isinstance(exc, SwitchUnavailable):
                    code = 'switch_unavailable'
                return fail(root, journal, record, code, stage)
    if not log_stage(root, {**record, 'stage': 'completed', 'outcome': 'committed'}):
        return fail(root, journal, record, 'post_commit_failed', 'completed')
    record = journal.update(operation_id, status='succeeded', stage='completed', outcome='committed')
    if barrier:
        barrier('outcome:succeeded')
    return record


def run(root, env_file, journal, authority, operation, steps=None, barrier=None, caller_holds_locks=False):
    operation_id = operation['id']
    try:
        with lifecycle_locks(root, caller_holds_locks):
            if caller_holds_locks and operation['status'] == 'running':
                current = next(r for r in journal.records() if r['id'] == operation_id)
                if current['status'] == 'running':
                    return advance(root, env_file, journal, authority, current, steps, barrier)
            record = journal.update(operation_id, if_status='queued', status='running')
            if record:
                return advance(root, env_file, journal, authority, record, steps, barrier)
    except BlockingIOError:
        failed = fail(root, journal, operation, 'lifecycle_busy', 'queued', if_status='queued')
        if failed:
            return failed
    return next(r for r in journal.records() if r['id'] == operation_id)


def recover(root, env_file, journal, authority, steps=None, barrier=None, caller_holds_locks=False):
    def interrupted():
        return [r for r in journal.records('running', 'recovery_required')
                if r.get('kind') == 'runtime_switch']

    if not interrupted():
        return []
    try:
        with lifecycle_locks(root, caller_holds_locks):
            # A live controller may have finished since the first read.
            return [advance(root, env_file, journal, authority, r, steps, barrier, recovering=True) for r in interrupted()]
    except BlockingIOError:
        return []


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if (len(args) != 3 or args[0] not in ('status', 'recover')) and (
            len(args) != 4 or args[0] != 'switch' or args[3] not in RUNTIMES):
        raise SystemExit('Usage: runtime_switch.py status|recover ROOT ENV | switch ROOT ENV django|go')
    action, root, env_file = args[:3]
    journal = Journal(Path(root) / '.updater/operations.sqlite3')
    if action == 'status':
        print(json.dumps(status(root, env_file, journal, None)))
        return 0

    host_lock = os.environ.get('ASSOZETA_RUNTIME_HOST_LOCK')
    try:
        with lifecycle_locks(root, host_lock == 'both', caller_holds_directory=host_lock == 'directory'):
            if action == 'recover':
                changed = recover(root, env_file, journal, None, caller_holds_locks=True)
                payload = status(root, env_file, journal, None)
                print(json.dumps(payload))
                return int(bool(payload['authority_error']) or any(r['status'] != 'succeeded' for r in changed))
            authority = compose_authority(root, env_file)
            row = authority.read()
            body = {'request_id': str(uuid4()), 'actor_id': OPERATOR_ACTOR, 'target': args[3], 'expected_generation': row.generation}
            code, payload = submit(root, env_file, journal, authority, body, claim=True)
            if code not in (200, 202):
                print(json.dumps(payload))
                return 1
            operation = payload['operation']
            if code == 202:
                operation = run(root, env_file, journal, authority, operation, caller_holds_locks=True)
            print(json.dumps(status(root, env_file, journal, authority)))
            return int(operation['outcome'] not in ('committed', 'noop'))
    except BlockingIOError:
        code = 'lifecycle_busy'
    except (AuthorityUnavailable, OSError):
        code = 'authority_unavailable'
    print(json.dumps({'error': ERRORS[code], 'code': code}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
