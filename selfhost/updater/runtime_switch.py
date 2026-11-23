"""Serialize instance runtime changes and reconcile them against the PostgreSQL owner."""
from collections import Counter, namedtuple
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import UUID, uuid4

import job_handoff
import runtime_adoption
import proxy_readiness
from common import atomic_write, compose_command, compose_environment, read_env, runtime_services
from runtime_schedules import schedule_blockers
from journal import Conflict, Journal, now

RUNTIMES = ('django', 'go')
OPERATOR_ACTOR = '00000000-0000-4000-8000-000000000001'
RECOVERY = './bin/assozeta runtime-recover'
Authority = namedtuple('Authority', 'runtime generation operation_id')
CommitResult = namedtuple('CommitResult', 'outcome authority')


class AuthorityUnavailable(Exception):
    pass


class StalePreference(Exception):
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
    counts = record.get('counts') or {}
    dispositions = record.get('dispositions') or {**empty_dispositions(), 'staged': counts.get('pending', 0),
        'transferred': counts.get('transferred', 0), 'blocked': counts.get('blocked', 0)}
    stage = record.get('failed_stage') or record['stage']
    index = PROGRESS_STAGES.index(stage) + 1 if stage in PROGRESS_STAGES else (10 if stage == 'completed' else 0)
    blockers = record.get('blockers')
    action = None
    if blockers and blockers['by_reason'].get('ambiguous_effect'):
        action = 'reconcile'
    elif record['status'] == 'recovery_required':
        action = 'recover'
    elif record['status'] == 'failed' and not record.get('superseded_by'):
        action = 'retry'
    return {**{key: record.get(key) for key in keys}, 'requested_runtime': record['target'],
            'source_generation': record.get('source_generation', record['expected_generation']),
            'failed_stage': record.get('failed_stage'), 'adoption': bool(record.get('adoption')),
            'progress': {'stage': stage, 'index': index, 'total': len(PROGRESS_STAGES),
                         'maintenance_since': record.get('maintenance_since'),
                         'maintenance_deadline': record.get('maintenance_deadline')},
            'dispositions': dispositions, 'blockers': blockers, 'recovery_action': action}


def submit(root, env_file, journal, authority, body, claim=False, *, adoption=None, parent_update=None):
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
            noop = adoption is None and row.runtime == body['target']
            request = {**body, 'kind': 'runtime_switch', 'source_generation': row.generation, 'previous_runtime': row.runtime,
                       'outcome': 'noop' if noop else None, 'committed_generation': None, 'error_code': None,
                       'failed_stage': None, 'counts': {'pending': 0, 'transferred': 0, 'blocked': 0}}
            if adoption is not None:
                request.update(adoption=True, legacy_services=adoption['legacy']['services'])
                if parent_update:
                    request['parent_update'] = parent_update
            pending = (Path(root) / '.updater/pending-distribution').exists()
            options = dict(blocked_reason='La transazione precedente richiede un ripristino.' if pending and adoption is None else None,
                           status='succeeded' if noop else ('running' if claim else 'queued'),
                           stage='completed' if noop else 'queued')
            try:
                record, replayed = journal.create_in(db, request, **options)
            except Conflict as exc:
                previous = exc.record or {}
                if not (exc.code == 'recovery_required' and previous.get('kind') == 'runtime_switch'
                        and previous['id'] == row.operation_id and previous['target'] == row.runtime
                        and previous.get('committed_generation') == row.generation == body['expected_generation']
                        and previous['previous_runtime'] == body['target']):
                    raise
                # Supersede and admit in the same transaction. All other
                # idempotency, lifecycle and generation checks still apply.
                journal.update_in(db, previous['id'], status='failed', stage='failed')
                request['reverses'] = previous['id']
                record, replayed = journal.create_in(db, request, **options)
                journal.update_in(db, previous['id'], superseded_by=record['id'])
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
    available = switch_available(root)
    # A stage can transfer many jobs. Polling must see receipts as they are
    # written, including held admissions bound by the authority transaction.
    if (available and not error and active and active.get('kind') == 'runtime_switch'
            and active['status'] == 'running' and active.get('maintenance_since')):
        try:
            live = receipt_dispositions(job_handoff.compose_store(root, env_file), active['id'])
        except (OSError, RuntimeError):
            pass  # The durable journal still reports the last completed batch.
        else:
            active = {**active, **disposition_fields(live)}
            if operation and operation['id'] == active['id']:
                operation = active
    requested = active.get('target') if active and active.get('kind') == 'runtime_switch' else None
    return {'protocol': 1, 'effective_runtime': row.runtime, 'generation': row.generation,
            'last_operation_id': row.operation_id, 'operation': operation_view(operation), 'active': operation_view(active),
            'requested_runtime': requested, 'intended_runtime': requested or row.runtime,
            'maintenance': (Path(root) / '.operations/maintenance.flag').is_file(),
            'switch_available': available, 'unavailable_reason': None if available else ERRORS['switch_unavailable'],
            'authority_error': error, 'recovery_command': RECOVERY}


PRE_COMMIT = ('preflight', 'close_admission', 'settle', 'stage_backlog', 'stop_source', 'target_preflight')
POST_COMMIT = ('activate', 'verify')
STEP_STAGES = PRE_COMMIT + POST_COMMIT
BARRIER_POINTS = (*('stage:' + stage for stage in PRE_COMMIT), 'stage:commit', 'authority:committed',
                  'stage:activate', 'stage:verify', 'outcome:succeeded')


STEP_POINTS = ('activate:started', 'verify:opened')
PROGRESS_STAGES = (*PRE_COMMIT, 'commit', *POST_COMMIT)


def switch_available(root):
    # Read the installed file directly: this status is also served when Docker
    # is unavailable. SafeLoader resolves the shipped YAML service anchors.
    try:
        data = (Path(root) / 'compose.yml').read_bytes()
    except OSError:
        return False
    import yaml
    try:
        config = yaml.safe_load(data)
        services = config['services'].values()
        declared = {profile for service in services if isinstance(service, dict)
                    for profile in service.get('profiles', ())}
        return set(RUNTIMES) <= declared
    except (AttributeError, KeyError, OSError, TypeError, yaml.YAMLError):
        return False


class RollbackBroker:
    """Restore missing broker copies after an interrupted staging batch or abort."""

    def __init__(self, broker, source):
        self.broker = broker
        source_queue = 'celery' if source == 'django' else 'assozeta-go'
        # Snapshot both ready and unacked copies before abort reopens admission.
        self.present = {job_handoff._message(raw)[0] for queue, _, _, _, raw in job_handoff._items(broker, 500)
                        if source is None or queue == source_queue}

    def call(self, command, *args):
        if command == 'LPUSH':
            task_id, _ = job_handoff._message(args[1])
            if task_id in self.present:
                return 0
        return self.broker.call(command, *args)


def default_steps(root, env_file, *, barrier=None):
    def unavailable(record):
        raise SwitchUnavailable()

    if not switch_available(root):
        def finish_historical(record):
            # Phase 1 could journal an already-committed authority with no
            # topology work. A newer operation must still verify its services.
            if record.get('maintenance_since') or (Path(root) / '.operations/maintenance.flag').exists():
                raise SwitchUnavailable()

        return {**dict.fromkeys((*PRE_COMMIT, 'abort'), unavailable), **dict.fromkeys(POST_COMMIT, finish_historical)}

    store = job_handoff.compose_store(root, env_file)
    broker = job_handoff.compose_broker(root, env_file)
    handoff = job_handoff.handoff_steps(store, broker)
    command = compose_command(root, env_file) + ['--profile', '*']
    environment = compose_environment(root, env_file)
    environment['ASSOZETA_GO_WHATSAPP_OUTBOX'] = '1'
    flag = Path(root) / '.operations/maintenance.flag'
    services = {}

    def execute(args, timeout=180, *, env=None):
        return subprocess.run(args, env=environment if env is None else env, capture_output=True, text=True,
                              check=True, timeout=timeout).stdout

    def app(runtime):
        if not services:
            config = json.loads(execute(command + ['config', '--format', 'json']))['services']
            for mode in RUNTIMES:
                services[mode] = runtime_services(root, env_file, mode, configured=config)
                if ('api-go' if mode == 'go' else 'api') not in services[mode]:
                    raise SwitchUnavailable()
        return services[runtime]

    def running():
        return set(execute(command + ['ps', '--status', 'running', '--services']).splitlines())

    def unsupported_schedules(target):
        return schedule_blockers(store, job_handoff._task_catalog()) if target == 'go' else []

    def preflight(record):
        if (not record.get('adoption') and record['target'] == 'django'
                and read_env(env_file).get('GO_TASK_QUEUE_TASKS', '').strip()):
            raise StalePreference()
        app(record['target'])
        problems = handoff['preflight'](record)['blockers']
        problems += unsupported_schedules(record['target'])
        if problems:
            raise job_handoff.TransferBlocked(problems)

    def drain(stage, record):
        timeout = min(60, max(0, remaining_window(record)))
        bounded = job_handoff.handoff_steps(store, broker, freeze_timeout=timeout, settle_timeout=timeout)
        return bounded[stage](record)

    def close_admission(record):
        atomic_write(flag, record['id'] + '\n', mode=0o644)
        if record.get('adoption') and record['legacy_services']:
            # Unmodified legacy consumers do not acknowledge the ledger freeze.
            # Quiesce them while Caddy serves maintenance, before reading queues.
            execute(command + ['stop', *record['legacy_services']])
        drain('close_admission', record)

    def stop(runtime):
        execute(command + ['stop', *app(runtime)])
        if running().intersection(app(runtime)):
            raise RuntimeError('source services still running')

    def start(runtime):
        execute(command + ['up', '-d', '--no-deps', *app(runtime)])

    def target_preflight(record):
        target = record['target']
        # An already-admitted source request may have edited a schedule during drain.
        problems = unsupported_schedules(target)
        if problems:
            raise job_handoff.TransferBlocked(problems)
        api = 'api-go' if target == 'go' else 'api'
        options = ['-e', 'ASSOZETA_GO_WHATSAPP_OUTBOX=0'] if target == 'go' else []
        # A foreground one-shot has neither service aliases nor published ports.
        # Probe its own API and exit, taking the private server down with it.
        server = '/app/server' if target == 'go' else './entrypoint.sh'
        probe = 'wget -T 2 -q -O /dev/null' if target == 'go' else 'curl -fsS --max-time 2'
        script = (f'{server} & pid=$!; for i in $(seq 1 60); do '
                  f'kill -0 "$pid" || exit 1; {probe} http://127.0.0.1:8000/readyz >/dev/null && exit 0; '
                  'sleep 1; done; exit 1')
        # Bound the process inside the container as well as the Compose client.
        budget = max(1, min(120, int(remaining_window(record)) + 1))
        execute(command + ['run', '--rm', '--no-deps', *options, '--entrypoint', 'timeout',
                           api, str(budget), 'sh', '-ec', script], timeout=budget + 30)

    def stage_backlog(record):
        def check_batch(point, task_id):
            # All source copies in this batch are removed before its acknowledgements.
            # Abort can now restore them without duplicating a runnable copy.
            if point == 'source:acked' and remaining_window(record) <= 0:
                raise TimeoutError()
        job_handoff.handoff_steps(store, broker, barrier=check_batch)['stage_backlog'](record)
        return {'dispositions': receipt_dispositions(store, record['id'])}

    def activate(record):
        atomic_write(flag, record['id'] + '\n', mode=0o644)
        if record.get('adoption'):
            # Start with the final producer routing while preserving the real
            # installation file until service and transfer verification succeeds.
            execute(command + ['up', '-d', '--no-deps', '--force-recreate', 'web'])
            clean_env = Path(root) / '.updater' / ('adopt-' + str(UUID(record['id'])) + '.env')
            runtime_adoption.retire_preferences(env_file, clean_env)
            try:
                execute(command + ['up', '-d', '--no-deps', *app(record['target'])],
                        env={**environment, 'ASSOZETA_ENV_FILE': str(clean_env)})
            finally:
                clean_env.unlink(missing_ok=True)
        else:
            start(record['target'])
        handoff['activate'](record)
        if barrier:
            barrier('activate:started')
        return {'dispositions': receipt_dispositions(store, record['id'])}

    def public_readiness():
        command, url = proxy_readiness.probe(root, env_file, execute)
        for endpoint in ('/api/readyz', '/api/v1/readyz', '/api/v2/readyz'):
            probe = command + [url + endpoint]
            deadline = time.monotonic() + 60
            while True:
                try:
                    execute(probe, timeout=15)
                    break
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)

    def inspect_runtime(runtime, inactive):
        target, source = app(runtime), app(inactive)
        actual = running()
        if actual.intersection(source) or not actual.issuperset(target):
            raise RuntimeError('unexpected runtime topology')
        # Compose reports a running process before its healthcheck succeeds.
        ids = execute(command + ['ps', '-q', *target]).split()
        deadline = time.monotonic() + 120
        while True:
            inspected = json.loads(execute(['docker', 'inspect', *ids]))
            observed = {item['Config']['Labels']['com.docker.compose.service'] for item in inspected}
            states = [item['State'] for item in inspected]
            if observed != set(target) or not all(state['Running'] for state in states):
                raise RuntimeError('target services unavailable')
            if all(state.get('Health', {}).get('Status', 'healthy') == 'healthy' for state in states):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('target readiness timed out')
            time.sleep(1)

    def verify(record):
        public_readiness()
        handoff['verify'](record)
        inspect_runtime(record['target'], 'go' if record['target'] == 'django' else 'django')
        evidence = {'dispositions': receipt_dispositions(store, record['id'])}
        if not log_stage(root, {**record, 'outcome': 'verified', **disposition_fields(evidence['dispositions'])}):
            raise RuntimeError('verification audit unavailable')
        if record.get('adoption'):
            runtime_adoption.retire_preferences(env_file)
        flag.unlink(missing_ok=True)
        if barrier:
            barrier('verify:opened')
        return evidence

    def stop_source(record):
        if record.get('adoption'):
            for runtime in RUNTIMES:
                stop(runtime)
        else:
            stop(record['previous_runtime'])

    def abort(record):
        stop(record['target'])
        source = None if record.get('adoption') else record['previous_runtime']
        job_handoff.abort(store, RollbackBroker(broker, source), record)
        if record.get('adoption'):
            previous = record['legacy_services']
            if previous:
                execute(command + ['start', *previous])
                public_readiness()
                if not running().issuperset(previous):
                    raise RuntimeError('legacy services unavailable')
                flag.unlink(missing_ok=True)
            return
        start(record['previous_runtime'])
        if not record.get('reverses'):
            public_readiness()
            inspect_runtime(record['previous_runtime'], record['target'])
            flag.unlink(missing_ok=True)

    return {'preflight': preflight, 'close_admission': close_admission, 'settle': lambda record: drain('settle', record),
            'stage_backlog': stage_backlog, 'stop_source': stop_source,
            'target_preflight': target_preflight, 'activate': activate, 'verify': verify, 'abort': abort}


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
    'stale_preference': 'Rimuovi GO_TASK_QUEUE_TASKS prima di passare a Django.',
    'switch_unavailable': 'Questa versione non supporta il cambio di runtime.',
    'authority_unavailable': 'Stato del runtime non disponibile.',
    'stale_generation': 'La generazione richiesta non è più attuale.',
    'authority_diverged': 'Il runtime corrente non corrisponde allo stato registrato.',
    'authority_unknown': 'Impossibile verificare il cambio di runtime.',
    'abort_failed': 'Il ripristino del runtime precedente richiede una verifica.',
    'transfer_blocked': 'Alcune attività impediscono il cambio di runtime.',
    'window_exceeded': 'È scaduto il tempo disponibile per il cambio di runtime.',
    'step_failed': 'Cambio di runtime non riuscito.',
    'post_commit_failed': 'Il nuovo runtime richiede una verifica.',
    'lifecycle_busy': 'Un’altra operazione è in corso.',
}


def fail(root, journal, record, code, stage, **changes):
    recoverable = code in ('authority_unknown', 'post_commit_failed', 'abort_failed')
    status = 'recovery_required' if recoverable else 'failed'
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        failed = journal.update_in(db, record['id'], status=status, stage=status, outcome='failed', failed_stage=stage,
                                   error=ERRORS[code], error_code=code, recovery=RECOVERY if recoverable else None, **changes)
        if failed and failed.get('reverses') and not recoverable and failed.get('committed_generation') is None:
            journal.update_in(db, failed['reverses'], status='recovery_required', stage='recovery_required',
                              superseded_by=None)
    if failed:
        log_stage(root, failed)
    return failed


def remaining_window(record):
    return (datetime.fromisoformat(record['maintenance_deadline']) - datetime.now(timezone.utc)).total_seconds()


def blocker_evidence(root, env_file, blockers):
    reasons = {item['task_id']: item['reason'] for item in blockers}
    # Read ages on the database clock in one snapshot. An item discovered only
    # in Redis, or an unsupported definition, has no accepted-at timestamp.
    try:
        store = job_handoff.compose_store(root, env_file)
        timestamps = 'SELECT task_id, accepted_at AS since FROM public.assozeta_handoff_work'
        if (any(ref.startswith('whatsapp:') and reason == 'ambiguous_effect' for ref, reason in reasons.items())
                and store.scalar("SELECT to_regclass('public.assozeta_outbox_effects') IS NOT NULL") == 't'):
            timestamps += """ UNION ALL SELECT outbox||':'||item_id, recorded_at FROM public.assozeta_outbox_effects
                              WHERE state IN ('intent','ambiguous')"""
        ids = [ref for ref, reason in reasons.items() if reason != 'unsupported_schedule']
        rows = store.rows(f"""SELECT task_id,
            greatest(0, floor(extract(epoch FROM statement_timestamp()-min(since))))::bigint AS age
            FROM ({timestamps}) stamps WHERE task_id IN (SELECT jsonb_array_elements_text(:'ids'::jsonb))
            GROUP BY task_id""", ids=json.dumps(ids))
    except (OSError, RuntimeError):
        # Keep the original blockers and finish rollback if the age lookup fails.
        rows = []
    ages = {row['task_id']: row['age'] for row in rows}
    items = sorted(({'ref': task, 'reason': reason, 'age_seconds': ages.get(task, 0)}
                    for task, reason in reasons.items()), key=lambda item: (-item['age_seconds'], item['ref']))
    by_reason = dict(Counter(reasons.values()))
    action = ('Verifica gli invii con esito incerto prima di riprovare.' if 'ambiguous_effect' in by_reason else
              'Correggi o disattiva le pianificazioni indicate e riprova.' if 'unsupported_schedule' in by_reason else
              'Verifica le attività bloccate e riprova il cambio di runtime.')
    return {'count': len(items), 'by_reason': by_reason, 'items': items[:20],
            'oldest_age_seconds': items[0]['age_seconds'] if items else 0, 'action': action}


def receipt_dispositions(store, operation_id):
    row, = store.rows("""SELECT count(*) FILTER (WHERE delivered_at IS NULL) AS staged,
        count(delivered_at) AS transferred,
        (SELECT count(*) FROM public.assozeta_handoff_work WHERE operation_id=:'op' AND state='blocked') AS blocked,
        (SELECT count(*) FROM public.assozeta_handoff_work WHERE operation_id=:'op' AND state='blocked'
         AND block_reason='ambiguous_effect') AS ambiguous
        FROM public.assozeta_handoff_transfers WHERE operation_id=:'op'""", op=operation_id)
    return row


def disposition_fields(dispositions):
    return {'dispositions': dispositions, 'counts': {'pending': dispositions['staged'],
            'transferred': dispositions['transferred'], 'blocked': dispositions['blocked']}}


def finish_abort(root, journal, record, steps):
    code, stage = record['abort_code'], record['abort_stage']
    try:
        steps['abort'](record)
    except Exception:
        return fail(root, journal, record, 'abort_failed', stage)
    counts = dict(record.get('dispositions') or empty_dispositions())
    counts['staged'] = 0
    return fail(root, journal, record, code, stage, abort_code=None, abort_stage=None, **disposition_fields(counts))


def empty_dispositions():
    return {'staged': 0, 'transferred': 0, 'blocked': 0, 'ambiguous': 0}


def advance(root, env_file, journal, authority, record, steps, barrier, recovering=False):
    installed = steps is None
    operation_id = record['id']
    source = record['previous_runtime'], record['source_generation']
    same = record.get('adoption') and record['target'] == source[0]
    destination = Authority(record['target'], source[1] + 1, operation_id)
    failed_stage = record.get('failed_stage') or record['stage']
    try:
        authority = authority or compose_authority(root, env_file)
        row = authority.read()
    except (AuthorityUnavailable, OSError):
        code = 'authority_unknown' if recovering else 'authority_unavailable'
        if recovering and record['status'] == 'recovery_required' and record.get('error_code') == code:
            return record
        # A reversal can supersede this record while the authority read is in
        # flight. Never reopen the old operation after the reversal is admitted.
        failed = fail(root, journal, record, code, failed_stage, if_status=record['status'])
        return failed or next(r for r in journal.records() if r['id'] == operation_id)

    if recovering and record['status'] == 'recovery_required':
        claimed = journal.update(operation_id, if_status='recovery_required', status='running')
        if not claimed:
            return next(r for r in journal.records() if r['id'] == operation_id)
        record = claimed
    committed = row == destination
    same_store = None
    if same and (row.runtime, row.generation) == source:
        same_store = job_handoff.compose_store(root, env_file)
        # Commit intent is journalled before the SQL transaction. Open
        # admission at that checkpoint distinguishes a lost commit reply.
        committed = record.get('committed_generation') == row.generation
        if not committed and failed_stage == 'commit' and not record.get('abort_code'):
            try:
                committed = same_store.scalar("SELECT admission FROM public.assozeta_go_job_ownership "
                                              "WHERE singleton=1") == 'open'
            except RuntimeError:
                return fail(root, journal, record, 'authority_unknown', failed_stage)
    if not committed and (row.runtime, row.generation) != source:
        return fail(root, journal, record, 'authority_diverged' if recovering else 'stale_generation', failed_stage)

    try:
        if installed:
            steps = default_steps(root, env_file, barrier=barrier)
    except Exception:
        return fail(root, journal, record, 'post_commit_failed' if committed else 'step_failed', failed_stage)
    if record.get('abort_code') and not committed:
        return finish_abort(root, journal, record, steps)

    stages = POST_COMMIT if committed else PROGRESS_STAGES
    for stage in stages:
        record = journal.update(operation_id, status='running', stage=stage, committed_generation=row.generation if committed else None,
                                outcome=None, failed_stage=None, error=None, error_code=None, recovery=None)
        try:
            if not log_stage(root, record):
                raise OSError()
            if barrier:
                barrier('stage:' + stage)
            if installed and stage == 'close_admission' and not record.get('maintenance_deadline'):
                since = datetime.now(timezone.utc)
                window = int(read_env(env_file).get('ASSOZETA_RUNTIME_SWITCH_WINDOW_SECONDS', 900))
                record = journal.update(operation_id, maintenance_since=since.isoformat(),
                                        maintenance_deadline=(since + timedelta(seconds=window)).isoformat())
            if not committed and record.get('maintenance_deadline') and remaining_window(record) <= 0:
                raise TimeoutError()
            if stage == 'commit':
                try:
                    if same:
                        job_handoff.bind_adoption(same_store, record)
                        destination = row
                        result = CommitResult('committed', row)
                    else:
                        result = authority.commit(operation_id, *source, record['target'])
                except (AuthorityUnavailable, OSError, RuntimeError):
                    return fail(root, journal, record, 'authority_unknown', stage)
                if result.outcome == 'conflict' or result.authority != destination:
                    return fail(root, journal, record, 'authority_diverged' if recovering else 'stale_generation', stage)
                row, committed = result.authority, True
                record = journal.update(operation_id, committed_generation=row.generation)
                if barrier:
                    barrier('authority:committed')
            else:
                evidence = steps[stage](record)
                if evidence and 'dispositions' in evidence:
                    record = journal.update(operation_id, blockers=None, **disposition_fields(evidence['dispositions']))
                if not committed and record.get('maintenance_deadline') and remaining_window(record) <= 0:
                    raise TimeoutError()
        except Exception as exc:
            if installed and committed:
                atomic_write(Path(root) / '.operations/maintenance.flag', operation_id + '\n', mode=0o644)
            code = 'post_commit_failed' if committed else 'step_failed'
            if isinstance(exc, job_handoff.TransferBlocked):
                code = 'post_commit_failed' if committed else 'transfer_blocked'
                summary = blocker_evidence(root, env_file, exc.blockers)
                counts = dict(record.get('dispositions') or empty_dispositions())
                counts.update(blocked=summary['count'], ambiguous=summary['by_reason'].get('ambiguous_effect', 0))
                record = journal.update(operation_id, blockers=summary, **disposition_fields(counts))
            elif not committed and record.get('maintenance_deadline') and remaining_window(record) <= 0:
                code = 'window_exceeded'
            elif isinstance(exc, StalePreference):
                code = 'stale_preference'
            elif isinstance(exc, SwitchUnavailable):
                code = 'switch_unavailable'
            if not committed and 'abort' in steps and (stage != 'preflight' or record.get('maintenance_since')):
                record = journal.update(operation_id, abort_code=code, abort_stage=stage)
                return finish_abort(root, journal, record, steps)
            return fail(root, journal, record, code, stage)
    if not log_stage(root, {**record, 'stage': 'completed', 'outcome': 'committed'}):
        if installed:
            atomic_write(Path(root) / '.operations/maintenance.flag', operation_id + '\n', mode=0o644)
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


def operation_barrier(directory, operation_id):
    if directory:
        mark = job_handoff._file_barrier(directory)
        return lambda point: mark(point, operation_id)
    return None


def recover(root, env_file, journal, authority, steps=None, barrier=None, caller_holds_locks=False, *, barrier_dir=None):
    def interrupted():
        return [r for r in journal.records('running', 'recovery_required')
                if r.get('kind') == 'runtime_switch']

    if not interrupted():
        return []
    try:
        with lifecycle_locks(root, caller_holds_locks):
            # A live controller may have finished since the first read.
            return [advance(root, env_file, journal, authority, r, steps,
                            barrier or operation_barrier(barrier_dir, r['id']), recovering=True) for r in interrupted()]
    except BlockingIOError:
        return []


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if (len(args) != 3 or args[0] not in ('status', 'recover', 'adopt-preflight', 'adopt')) and (
            len(args) != 4 or args[0] != 'switch' or args[3] not in RUNTIMES):
        raise SystemExit('Usage: runtime_switch.py status|recover|adopt-preflight|adopt ROOT ENV | switch ROOT ENV django|go')
    action, root, env_file = args[:3]
    if action in ('adopt-preflight', 'adopt'):
        try:
            report = runtime_adoption.preflight(root, env_file, report_inventory=action == 'adopt-preflight')
        except runtime_adoption.AdoptionInterrupted:
            print(json.dumps({'code': 'recovery_required', 'recovery_command': RECOVERY}))
            return 1
        except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
            print(json.dumps({'error': ERRORS['authority_unavailable']}))
            return 1
        if action == 'adopt-preflight' or report['blockers'] or not report['adoption_required']:
            print(json.dumps(report))
            return 3 if report['blockers'] else 0
    journal = Journal(Path(root) / '.updater/operations.sqlite3')
    if action == 'status':
        print(json.dumps(status(root, env_file, journal, None)))
        return 0

    directory = os.environ.get('ASSOZETA_HANDOFF_BARRIER_DIR')
    host_lock = os.environ.get('ASSOZETA_RUNTIME_HOST_LOCK')
    try:
        with lifecycle_locks(root, host_lock == 'both', caller_holds_directory=host_lock == 'directory'):
            if action == 'recover':
                changed = recover(root, env_file, journal, None, caller_holds_locks=True, barrier_dir=directory)
                payload = status(root, env_file, journal, None)
                print(json.dumps(payload))
                return int(bool(payload['authority_error']) or any(r['status'] != 'succeeded' for r in changed))
            authority = compose_authority(root, env_file)
            row = authority.read()
            adoption = None
            parent_update = None
            if action == 'adopt':
                adoption = report if host_lock == 'both' else runtime_adoption.preflight(
                    root, env_file, report_inventory=False)
                if adoption['blockers'] or not adoption['adoption_required']:
                    print(json.dumps(adoption))
                    return 3 if adoption['blockers'] else 0
                pending = Path(root) / '.updater/pending-distribution/transaction.json'
                if pending.exists():
                    transaction = json.loads(pending.read_text())
                    if (os.environ.get('ASSOZETA_RUNTIME_UPGRADE') != '1'
                            or transaction['phase'] != 'migration_started'):
                        raise BlockingIOError()
                    parent_update = transaction.get('operation_id')
            body = {'request_id': str(uuid4()), 'actor_id': OPERATOR_ACTOR,
                    'target': adoption['requested_runtime'] if adoption else args[3], 'expected_generation': row.generation}
            code, payload = submit(root, env_file, journal, authority, body, claim=True, adoption=adoption,
                                   parent_update=parent_update)
            if code not in (200, 202):
                print(json.dumps(payload))
                return 1
            operation = payload['operation']
            if code == 202:
                operation = run(root, env_file, journal, authority, operation,
                                barrier=operation_barrier(directory, operation['id']), caller_holds_locks=True)
            print(json.dumps(status(root, env_file, journal, authority)))
            return int(operation['outcome'] not in ('committed', 'noop'))
    except BlockingIOError:
        code = 'lifecycle_busy'
    except runtime_adoption.AdoptionInterrupted:
        print(json.dumps({'code': 'recovery_required', 'recovery_command': RECOVERY}))
        return 1
    except (AuthorityUnavailable, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError):
        code = 'authority_unavailable'
    print(json.dumps({'error': ERRORS[code], 'code': code}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
