"""Transfer accepted instance jobs between the Django and Go runtimes."""
import argparse
import hashlib
import os
from pathlib import Path
import tempfile
from itertools import islice
import base64
import json
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone

RESET_TASK = 'reset_communication_daily_email_balance'
RENEWAL_TASK = 'renew_memberships_payments'
QUEUES = ('celery', 'assozeta-go')
# Django migration 0430 retired both schedules and left no-op task bodies.
RETIRED_TASKS = frozenset(('auto_move_users_to_base_plan', 'nurturing_warm_leads'))
LEGACY_DJANGO_TASKS = frozenset(('save_binary_to_storage', 'send_updated_certificate_email'))


def _task_catalog():
    path = Path(__file__).with_name('go-tasks-inventory.json')
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / 'docs/architecture/go-migration/go-tasks-inventory.json'
    return {r['name']: r for r in json.loads(path.read_bytes())}
PRIORITIES = ('', '\x06\x163', '\x06\x166', '\x06\x169')


class TransferBlocked(Exception):
    def __init__(self, blockers):
        self.blockers = blockers
        super().__init__(f'{len(blockers)} jobs block transfer')


class PsqlStore:
    def __init__(self, command, environment=None):
        self.command = [*command, '-X', '-qAt', '-v', 'ON_ERROR_STOP=1']
        self.environment = environment

    def execute(self, sql, **variables):
        # Keep bind values on stdin. psql's :'name' syntax supplies SQL literal
        # quoting; escape values separately for the single-quoted metacommand.
        commands = []
        for name, value in variables.items():
            value = str(value).replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')
            commands.append(f"\\set {name} '{value}'")
        commands.append(sql + ';')
        try:
            result = subprocess.run(self.command, input='\n'.join(commands) + '\n', env=self.environment,
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('handoff database unavailable') from None
        if result.returncode:
            raise RuntimeError('handoff database operation failed')
        return result.stdout

    def rows(self, sql, **variables):
        return json.loads(self.execute(f"SELECT coalesce(json_agg(r), '[]') FROM ({sql}) r", **variables))

    def scalar(self, sql, **variables):
        return self.execute(sql, **variables).strip()


class RespBroker:
    def __init__(self, host, port, *, username=None, password=None, db=0, timeout=10.0):
        self.address = (host, port)
        self.username, self.password = username, password
        self.db, self.timeout = db, timeout

    def call(self, *arguments):
        # A command's reply can be lost after it has taken effect. The caller
        # owns retries against its receipt; never replay a command on reconnect.
        with socket.create_connection(self.address, self.timeout) as sock, sock.makefile('rb') as stream:
            def send(*parts):
                frame = [b'*%d\r\n' % len(parts)]
                for part in parts:
                    data = part if isinstance(part, bytes) else str(part).encode()
                    frame.extend((b'$%d\r\n' % len(data), data, b'\r\n'))
                sock.sendall(b''.join(frame))
                return self._reply(stream)

            if self.password is not None:
                if self.username:
                    send('AUTH', self.username, self.password)
                else:
                    send('AUTH', self.password)
            if self.db:
                send('SELECT', self.db)
            return send(*arguments)

    @classmethod
    def _reply(cls, stream):
        line = stream.readline()
        if not line.endswith(b'\r\n'):
            raise RuntimeError('incomplete broker reply')
        kind, value = line[:1], line[1:-2]
        if kind == b'+':
            return value
        if kind == b':':
            return int(value)
        if kind in (b'*', b'$'):
            size = int(value)
            if size == -1:
                return None
            if size < 0:
                raise RuntimeError('invalid broker reply')
            if kind == b'*':
                return [cls._reply(stream) for _ in range(size)]
            data = stream.read(size)
            if len(data) != size or stream.read(2) != b'\r\n':
                raise RuntimeError('incomplete broker value')
            return data
        raise RuntimeError('broker command failed')


def _items(broker, batch_size):
    # Visibility restore atomically moves an unacked item back to a list.
    # Visit the hash first. A repeat after removal catches concurrent moves.
    cursor = b'0'
    while True:
        cursor, pairs = broker.call('HSCAN', 'unacked', cursor, 'COUNT', batch_size)
        for tag, value in zip(pairs[::2], pairs[1::2]):
            try:
                message, _, queue = json.loads(value)
            except (TypeError, ValueError, UnicodeError):
                continue
            if queue in QUEUES:
                yield queue, 'unacked', tag, value, json.dumps(message).encode()
        if cursor == b'0':
            break
    for queue in QUEUES:
        for suffix in PRIORITIES:
            key = queue + suffix
            offset = 0
            while True:
                page = broker.call('LRANGE', key, -offset-batch_size, -offset-1)
                for raw in reversed(page):
                    yield queue, 'queue', key, raw, raw
                if len(page) < batch_size:
                    break
                offset += batch_size


def _message(raw):
    try:
        msg = json.loads(raw)
        headers = msg['headers']
        if isinstance(headers['id'], str) and 0 < len(headers['id'].encode()) <= 255:
            return headers['id'], msg
    except (KeyError, TypeError, ValueError, UnicodeError):
        pass
    return 'opaque-' + hashlib.sha256(raw).hexdigest(), {}


def _renewal_arguments(args, kwargs):
    names = ('attempt', 'max_attempts')
    if len(args) > len(names) or any(name in kwargs for name in names[:len(args)]):
        raise ValueError('invalid renewal arguments')
    arguments = dict(zip(names, args), **kwargs)
    if any(name not in names or type(value) is not int or value < 1 for name, value in arguments.items()):
        raise ValueError('invalid renewal arguments')
    return arguments


def _reason(message, catalog, target):
    headers = message.get('headers', {})
    name = headers.get('task')
    if not isinstance(name, str) or name not in catalog:
        return 'unknown_task'
    properties = message.get('properties', {})
    if (message.get('content-type') != 'application/json' or
            message.get('content-encoding', '').lower() not in ('utf-8', 'utf8') or
            properties.get('body_encoding') != 'base64' or headers.get('compression')):
        return 'unsupported_encoding'
    try:
        args, kwargs, options = json.loads(base64.b64decode(message['body'], validate=True))
        if not isinstance(args, list) or not isinstance(kwargs, dict) or not isinstance(options, dict):
            return 'unsupported_encoding'
    except (KeyError, TypeError, ValueError, UnicodeError):
        return 'unsupported_encoding'
    if target == 'go':
        if name in LEGACY_DJANGO_TASKS:
            return 'retired_task'
        if name not in RETIRED_TASKS and not (catalog[name]['consumer'] or catalog[name]['job']):
            return 'no_target_handler'
        if any(options.values()) or any(headers.get(k) is not None for k in ('shadow','group','group_index','stamped_headers')):
            return 'unsupported_option'
        if name == RENEWAL_TASK:
            try:
                _renewal_arguments(args, kwargs)
            except ValueError:
                return 'unsupported_input'
        elif catalog[name]['job'] and not catalog[name]['consumer'] and args:
            return 'unsupported_option'
    return None


def _copies(broker, batch_size, task_ids):
    found = {task: [] for task in task_ids}
    for _, kind, key, value, raw in _items(broker, batch_size):
        task, _ = _message(raw)
        if task in found:
            found[task].append((kind, key, value))
    return found


def _remove(broker, copies):
    for kind, key, raw in copies:
        if kind == 'queue':
            broker.call('LREM', key, 0, raw)
        else:
            broker.call('EVAL', """if redis.call('HGET',KEYS[1],ARGV[1]) == ARGV[2] then
                redis.call('HDEL',KEYS[1],ARGV[1])
                return redis.call('ZREM',KEYS[2],ARGV[1])
                end return 0""", 2, 'unacked', 'unacked_index', key, raw)


def _envelope(job):
    task = job['task_id']
    due = datetime.fromisoformat(job['available_at'])
    eta = due.isoformat() if due > datetime.now(timezone.utc) else None
    body = [[], job['payload'], {'callbacks': None, 'errbacks': None, 'chain': None, 'chord': None}]
    return json.dumps({
        'body': base64.b64encode(json.dumps(body).encode()).decode(),
        'content-encoding': 'utf-8', 'content-type': 'application/json',
        'headers': {'lang': 'py', 'task': job['task_name'], 'id': task, 'eta': eta, 'expires': job.get('expires'),
                    'retries': job['attempts'], 'root_id': job.get('root_id') or task, 'parent_id': job.get('parent_id'), 'timelimit': [None, None],
                    'assozeta_published_at': datetime.fromisoformat(job['created_at']).timestamp()},
        'properties': {'correlation_id': task, 'delivery_mode': 2, 'delivery_tag': str(uuid.uuid4()),
                       'body_encoding': 'base64', 'priority': job.get('priority') or 0,
                       'delivery_info': {'exchange': '', 'routing_key': 'celery'}},
    }).encode()


def bind_adoption(store, record):
    """Bind the frozen backlog without changing a same-runtime authority."""
    # Every write depends on acquiring this operation's admission fence.
    result = store.scalar("""BEGIN;
        SELECT 1 AS locked FROM public.assozeta_go_job_ownership WHERE singleton=1 FOR UPDATE \\gset
        WITH fence AS (
        UPDATE public.assozeta_go_job_ownership SET admission='open',admission_operation_id=NULL
        WHERE singleton=1 AND runtime=:'target' AND generation=:'gen'
          AND admission='frozen' AND admission_operation_id=:'op' RETURNING generation),
        held AS (
        INSERT INTO public.assozeta_handoff_transfers(operation_id,task_id,source_runtime,source_generation,
                                                       target_runtime,target_generation,source_location)
        SELECT :'op',w.task_id,:'target',generation,:'target',generation,
               CASE w.transport WHEN 'sql' THEN 'sql' ELSE 'ledger' END
        FROM public.assozeta_handoff_work w CROSS JOIN fence WHERE w.operation_id=:'op' AND w.state='held'),
        bound AS (
        UPDATE public.assozeta_handoff_work SET state='pending',owner_runtime=:'target',
               owner_generation=fence.generation,updated_at=clock_timestamp() FROM fence
        WHERE operation_id=:'op' AND state IN ('held','staged')),
        receipts AS (
        UPDATE public.assozeta_handoff_transfers SET target_generation=fence.generation FROM fence
        WHERE operation_id=:'op')
        SELECT count(*) FROM fence; COMMIT""", op=record['id'], gen=record['source_generation'], target=record['target'])
    if result != '1':
        raise RuntimeError('adoption admission conflict')


def handoff_steps(store, broker, *, barrier=None, batch_size=500, freeze_timeout=60.0, settle_timeout=60.0,
                  heartbeat_grace=5.0):
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    catalog = _task_catalog()
    mark = barrier or (lambda point, key: None)

    def params(record):
        return dict(op=record['id'], source=record['previous_runtime'], gen=record['source_generation'],
                    target=record['target'], adoption=bool(record.get('adoption')))

    def close_admission(record):
        values = params(record)
        result = store.scalar("SELECT public.assozeta_handoff_close_admission(:'op', :'source', :'gen')", **values)
        if result not in ('closed', 'already_closed'):
            raise RuntimeError('handoff admission conflict')
        deadline = time.monotonic() + freeze_timeout
        while True:
            counts, = store.rows("""SELECT count(*) AS live,
                count(*) FILTER (WHERE freeze_operation_id=:'op') AS acknowledged FROM public.assozeta_handoff_consumers
                WHERE runtime=:'source' AND (freeze_operation_id=:'op' OR
                heartbeat_at >= clock_timestamp() - make_interval(secs => :'grace'))""", grace=heartbeat_grace, **values)
            if counts['live'] == counts['acknowledged']:
                return {'acknowledged': counts['acknowledged']}
            if time.monotonic() >= deadline:
                raise RuntimeError('source consumers have not acknowledged the freeze')
            time.sleep(0.05)

    def task_rows(query, **values):
        after = ''
        while True:
            rows = store.rows(f"""SELECT page.* FROM ({query}) page WHERE task_id>:'after'
                ORDER BY task_id LIMIT :'batch'""", after=after, batch=batch_size, **values)
            if not rows:
                return
            yield from rows
            after = rows[-1]['task_id']

    def outbox_blockers(record, *, settled=False):
        if store.scalar("SELECT count(*) WHERE to_regclass('public.assozeta_outbox_effects') IS NOT NULL") != '1':
            return []
        # A live sender holds the item lock through confirmation. Its intent
        # becomes a blocker if the sender disappears before the next poll.
        return list(task_rows("""SELECT outbox || ':' || item_id AS task_id,'ambiguous_effect' AS reason
            FROM public.assozeta_outbox_effects WHERE state='ambiguous' OR
              (state='intent' AND (ownership_generation < :'gen' OR
                (:'settled'::boolean AND ownership_generation = :'gen') OR
                pg_try_advisory_xact_lock(hashtextextended(item_id,8))))""", settled=settled, **params(record)))

    def blockers(record):
        return list(task_rows("""SELECT task_id,block_reason AS reason FROM public.assozeta_handoff_work
            WHERE state='blocked' AND ((owner_runtime=:'source' AND owner_generation=:'gen') OR operation_id=:'op')""",
                              **params(record))) + outbox_blockers(record, settled=True)

    def settle(record):
        deadline = time.monotonic() + settle_timeout
        while True:
            store.execute("""WITH releasable AS MATERIALIZED (
                SELECT a.attempt_id FROM public.assozeta_handoff_attempts a
                JOIN public.assozeta_go_job_ownership o ON o.runtime=a.runtime AND o.generation=a.generation
                    AND o.admission_operation_id=:'op'
                LEFT JOIN public.assozeta_handoff_consumers c USING(consumer_id)
                LEFT JOIN public.assozeta_go_jobs j ON j.lease_token=a.attempt_id AND j.state='running'
                WHERE a.runtime=:'source' AND a.generation=:'gen' AND a.outcome='running'
                    AND CASE WHEN a.lease_until IS NOT NULL THEN a.lease_until<=clock_timestamp()
                        ELSE (c.heartbeat_at IS NULL OR c.heartbeat_at < clock_timestamp()-make_interval(secs=>:'grace'))
                            AND (j.lease_until IS NULL OR j.lease_until<=clock_timestamp())
                        END
                LIMIT :'batch' FOR UPDATE OF a SKIP LOCKED),
                released AS MATERIALIZED (
                    SELECT attempt_id, public.assozeta_handoff_finish(attempt_id,'released') FROM releasable),
                cleared AS (UPDATE public.assozeta_go_jobs j SET state='pending',lease_owner='',
                    lease_token='00000000-0000-0000-0000-000000000000',lease_until=NULL
                FROM released WHERE j.lease_token=released.attempt_id AND j.state='running')
                SELECT count(*) FROM released""",
                          grace=heartbeat_grace, batch=batch_size, **params(record))
            running = int(store.scalar("""SELECT count(*) FROM public.assozeta_handoff_attempts
                WHERE runtime=:'source' AND generation=:'gen' AND outcome='running'""", **params(record)))
            historical_running = []
            if record['previous_runtime'] == 'go' or record.get('adoption'):
                # Pre-ledger workers have no consumer registration. Their SQL
                # lease token is the fence; invalidate it only after expiry.
                store.execute("""UPDATE public.assozeta_go_jobs j SET state='pending',lease_owner='',
                    lease_token='00000000-0000-0000-0000-000000000000',lease_until=NULL
                    WHERE j.state='running' AND j.lease_until<=clock_timestamp() AND NOT EXISTS
                    (SELECT 1 FROM public.assozeta_handoff_attempts a WHERE a.attempt_id=j.lease_token AND a.outcome='running')
                    AND EXISTS (SELECT 1 FROM public.assozeta_go_job_ownership WHERE runtime=:'source' AND generation=:'gen' AND admission_operation_id=:'op')""",
                              **params(record))
                historical_running = list(task_rows("""SELECT j.id::text AS task_id,'running' AS reason FROM public.assozeta_go_jobs j
                    WHERE j.state='running' AND NOT EXISTS (SELECT 1 FROM public.assozeta_handoff_attempts a
                    WHERE a.attempt_id=j.lease_token AND a.outcome='running')"""))
            if not running and not historical_running:
                # Print callers reconcile ambiguous requests before resuming settle.
                print_blockers = list(task_rows("""SELECT w.task_id, w.block_reason AS reason
                    FROM public.assozeta_handoff_work w
                    WHERE w.owner_runtime=:'source' AND w.owner_generation=:'gen'
                      AND w.state='blocked' AND w.block_reason='ambiguous_effect'
                      AND EXISTS (SELECT 1 FROM public.assozeta_handoff_effects e
                        WHERE e.task_id=w.task_id AND e.kind='print' AND e.state='ambiguous')""", **params(record)))
                if print_blockers:
                    raise TransferBlocked(print_blockers)
                return {'running': 0}
            if time.monotonic() >= deadline:
                raise TransferBlocked(historical_running + list(task_rows("""SELECT task_id,'running' AS reason FROM public.assozeta_handoff_attempts
                    WHERE runtime=:'source' AND generation=:'gen' AND outcome='running'""", **params(record))))
            time.sleep(0.05)

    def ledger_pages(values):
        return task_rows("""SELECT task_id,task_name,transport,state,block_reason,encode(envelope,'base64') AS envelope
            FROM public.assozeta_handoff_work WHERE ((owner_runtime=:'source' AND owner_generation=:'gen')
            OR operation_id=:'op') AND state IN ('pending','held','staged','blocked')""", **values)

    def row_reason(row, target):
        if row['state'] == 'blocked':
            return row['block_reason']
        if row['transport'] == 'broker':
            _, msg = _message(base64.b64decode(row['envelope'] or ''))
            return _reason(msg, catalog, target)
        if row['task_name'] not in catalog:
            return 'unknown_task'
        version = store.scalar("SELECT version FROM public.assozeta_go_jobs WHERE id::text=:'task'", task=row['task_id'])
        return None if version == '1' else 'unsupported_option'

    def preflight(record):
        values = params(record)
        problems = {r['task_id']: r['reason'] for r in outbox_blockers(record)}
        for _, _, _, _, raw in _items(broker, batch_size):
            task, msg = _message(raw)
            reason = _reason(msg, catalog, values['target'])
            if reason:
                problems[task] = reason
        for row in historical(values):
            if row['task_name'] not in catalog:
                problems[row['task_id']] = 'unknown_task'
            elif row['version'] != 1:
                problems[row['task_id']] = 'unsupported_option'
        for row in ledger_pages(values):
            reason = row_reason(row, values['target'])
            if reason:
                problems[row['task_id']] = reason
        return {'blockers': [{'task_id': task, 'reason': reason} for task, reason in sorted(problems.items())]}

    def historical(values):
        if values['source'] != 'go' and not values['adoption']:
            return
        yield from task_rows("""SELECT j.id::text AS task_id,j.kind AS task_name,j.version,j.state,j.attempts,
            j.association_id,j.created_at,j.available_at FROM public.assozeta_go_jobs j
            WHERE j.state IN ('pending','running') AND NOT EXISTS
            (SELECT 1 FROM public.assozeta_handoff_work w WHERE w.task_id=j.id::text)""")

    def discover(values):
        for row in historical(values):
            if row['state'] == 'running':
                raise TransferBlocked([{'task_id': row['task_id'], 'reason': 'running'}])
            store.execute("""INSERT INTO public.assozeta_handoff_work(task_id,task_name,transport,origin_runtime,
                owner_runtime,owner_generation,state,accepted_at,eta,retries,association_id)
                VALUES(:'task_id',:'task_name','sql','go',:'source',:'gen','pending',:'created_at',:'available_at',:'attempts',:'association_id')
                ON CONFLICT DO NOTHING""", **row, **values)

        # Inventory both historical queue names before removing anything. The
        # position is the BRPOP order, independent of wall clocks and UUIDs.
        native_renewal_copies = []
        for pos, (queue, kind, key, value, raw) in enumerate(_items(broker, batch_size)):
            task, msg = _message(raw)
            headers = msg.get('headers', {})
            name = headers.get('task') or 'unknown'
            if values['source'] == 'go' and name == RENEWAL_TASK and store.scalar("""
                SELECT EXISTS(SELECT 1 FROM public.assozeta_handoff_work w
                    JOIN public.assozeta_go_jobs j ON j.id::text=w.task_id
                    WHERE w.task_id=:'task' AND w.task_name='renew_memberships_payments'
                    AND w.owner_runtime='go' AND w.owner_generation=:'gen' AND w.transport='sql')""",
                    task=task, **values) == 't':
                # A crash between native admission and broker ACK can leave the
                # old delivery. The SQL receipt is already independently durable.
                native_renewal_copies.append((kind, key, value))
                continue
            store.execute("""INSERT INTO public.assozeta_handoff_work AS w
                (task_id,task_name,transport,queue,envelope,origin_runtime,owner_runtime,owner_generation,state,
                 accepted_at,eta,expires,retries,priority,parent_id,root_id,source_position)
                VALUES (:'task',:'name','broker',:'queue',decode(:'raw','base64'),:'source',:'source',:'gen','pending',
                 to_timestamp(:'stamp'),NULLIF(:'eta','')::timestamptz,NULLIF(:'expires','')::timestamptz,
                 :'retries',:'priority',NULLIF(:'parent',''),NULLIF(:'root',''),:'pos')
                ON CONFLICT(task_id) DO UPDATE SET source_position=coalesce(w.source_position,excluded.source_position)
                WHERE w.state='pending' AND w.owner_runtime=:'source' AND w.owner_generation=:'gen'""",
                          task=task, name=name, queue=queue, raw=base64.b64encode(raw).decode(),
                          stamp=headers.get('assozeta_published_at') or 0, eta=headers.get('eta') or '',
                          expires=headers.get('expires') or '', retries=headers.get('retries') or 0,
                          priority=msg.get('properties', {}).get('priority') or 0, parent=headers.get('parent_id') or '',
                          root=headers.get('root_id') or '', pos=pos, **values)
        _remove(broker, native_renewal_copies)

    def stage_backlog(record):
        values = params(record)
        frozen = store.scalar("""SELECT count(*) FROM public.assozeta_go_job_ownership
            WHERE runtime=:'source' AND generation=:'gen' AND admission_operation_id=:'op'""", **values)
        if frozen != '1':
            raise RuntimeError('source admission must be frozen before staging')
        discover(values)
        staged = 0
        pending = ledger_pages(values)
        while batch := list(islice(pending, batch_size)):
            reasons = {row['task_id']: row_reason(row, values['target']) for row in batch}
            refusals = [{'task_id': row['task_id'], 'reason': 'retired_task'} for row in batch
                        if reasons[row['task_id']] == 'retired_task']
            if refusals:
                # Leave the source delivery runnable on abort. The ledger also
                # holds the exact envelope if it came from an older release.
                raise TransferBlocked(refusals)
            broker_ids = {row['task_id'] for row in batch if row['transport'] == 'broker'}
            copies = _copies(broker, batch_size, broker_ids) if broker_ids else {}
            for row in batch:
                task = row['task_id']
                reason = reasons[task]
                if reason:
                    store.execute("""UPDATE public.assozeta_handoff_work SET state='blocked',block_reason=:'reason',operation_id=:'op'
                        WHERE task_id=:'task'""", task=task, reason=reason, **values)
                elif row['state'] == 'held':
                    # Commit binds even admissions that arrive after our last batch.
                    continue
                elif row['state'] == 'pending':
                    locations = copies.get(task, [])
                    location = 'sql' if row['transport'] == 'sql' else locations[0][0] if locations else 'ledger'
                    store.execute("""WITH staged AS (
                        UPDATE public.assozeta_handoff_work SET state='staged',operation_id=:'op',updated_at=clock_timestamp()
                        WHERE task_id=:'task' AND state='pending' AND owner_runtime=:'source' AND owner_generation=:'gen'
                        RETURNING task_id)
                        INSERT INTO public.assozeta_handoff_transfers
                        (operation_id,task_id,source_runtime,source_generation,target_runtime,source_location)
                        SELECT :'op',task_id,:'source',:'gen',:'target',:'location' FROM staged ON CONFLICT DO NOTHING""",
                                  task=task, location=location, **values)
                    mark('receipt:persisted', task)
                staged += 1
            while any(copies.values()):
                for locations in copies.values():
                    _remove(broker, locations)
                copies = _copies(broker, batch_size, broker_ids)
            for task in broker_ids:
                store.execute("""UPDATE public.assozeta_handoff_transfers SET source_removed_at=coalesce(source_removed_at,clock_timestamp())
                    WHERE operation_id=:'op' AND task_id=:'task'""", task=task, **values)
                mark('source:acked', task)
        blocked = blockers(record)
        if blocked:
            raise TransferBlocked(blocked)
        return {'staged': staged}

    def require_target(values):
        same = values['adoption'] and values['source'] == values['target']
        committed = store.scalar("""SELECT count(*) FROM public.assozeta_go_job_ownership
            WHERE runtime=:'target' AND generation=:'destination_gen' AND admission='open'
              AND (:'same'::boolean OR operation_id=:'op')""",
                                 destination_gen=values['gen'] + (0 if same else 1), same=same, **values)
        if committed != '1':
            raise RuntimeError('target ownership must commit before delivery')

    def activate(record):
        values = params(record)
        require_target(values)
        delivered = 0
        while True:
            rows = store.rows("""SELECT w.task_id,w.task_name,w.transport,w.accepted_at,w.eta,w.retries,
                       encode(w.envelope,'base64') AS envelope,w.priority,w.root_id,w.parent_id,w.expires,j.payload,j.attempts,j.created_at,j.available_at
                FROM public.assozeta_handoff_transfers t JOIN public.assozeta_handoff_work w USING(task_id)
                LEFT JOIN public.assozeta_go_jobs j ON j.id::text=w.task_id
                WHERE t.operation_id=:'op' AND t.delivered_at IS NULL AND w.owner_runtime=:'target'
                  AND w.owner_generation=t.target_generation ORDER BY w.source_position NULLS LAST,w.accepted_at,w.task_id LIMIT :'batch'""",
                              batch=batch_size, **values)
            if not rows:
                return {'delivered': delivered}
            for row in rows:
                task = row['task_id']
                native = values['target'] == 'go' and (row['task_name'] == RENEWAL_TASK or not catalog[row['task_name']]['consumer'])
                if values['target'] == 'go' and row['task_name'] in RETIRED_TASKS:
                    store.execute("""UPDATE public.assozeta_handoff_work
                        SET state='cancelled',block_reason='retired_task',updated_at=clock_timestamp()
                        WHERE task_id=:'task' AND state='pending'""", task=task)
                    delivery = 'retired:' + task
                elif native:
                    kwargs = {}
                    if row['envelope']:
                        message = json.loads(base64.b64decode(row['envelope']))
                        args, kwargs, _ = json.loads(base64.b64decode(message['body']))
                        if row['task_name'] == RENEWAL_TASK:
                            kwargs = _renewal_arguments(args, kwargs)
                    store.execute("""BEGIN;
                        INSERT INTO public.assozeta_go_jobs (id,association_id,kind,version,idempotency_key,payload,
                          max_attempts,attempts,available_at,created_at)
                        SELECT task_id::uuid,coalesce(association_id,'00000000-0000-0000-0000-000000000000'),task_name,
                            1,'handoff:'||task_id,:'kwargs'::jsonb,greatest(3,retries+1),retries,coalesce(eta,accepted_at),
                            accepted_at FROM public.assozeta_handoff_work WHERE task_id=:'task'
                        ON CONFLICT (id) DO UPDATE SET state='pending',error_code='',finished_at=NULL,
                          attempts=excluded.attempts,available_at=excluded.available_at
                          WHERE assozeta_go_jobs.state='cancelled' AND assozeta_go_jobs.error_code='handoff_transferred';
                        UPDATE public.assozeta_handoff_work SET transport='sql',queue=NULL,envelope=NULL,
                          updated_at=clock_timestamp() WHERE task_id=:'task';
                        COMMIT""", task=task, kwargs=json.dumps(kwargs))
                    delivery = 'sql:' + task
                else:
                    queue = 'assozeta-go' if values['target'] == 'go' else 'celery'
                    if row['transport'] == 'sql':
                        payload = _envelope(row)
                    else:
                        message = json.loads(base64.b64decode(row['envelope']))
                        message['properties']['delivery_info']['routing_key'] = queue
                        payload = json.dumps(message).encode()
                    # Commit the representation before the consumer can see it.
                    # A crash here leaves an undelivered receipt for the rerun.
                    store.execute("""BEGIN;
                        UPDATE public.assozeta_go_jobs SET state='cancelled',error_code='handoff_transferred',
                            finished_at=clock_timestamp() WHERE id::text=:'task' AND state='pending' AND (:'source'='go' OR :'adoption'::boolean);
                        UPDATE public.assozeta_handoff_work SET transport='broker',queue=:'queue',
                            envelope=decode(:'payload','base64'),updated_at=clock_timestamp() WHERE task_id=:'task';
                        COMMIT""", task=task, queue=queue, payload=base64.b64encode(payload).decode(), **values)
                    suffix = PRIORITIES[min(9, max(0, row['priority'] or 0)) // 3]
                    broker.call('LPUSH', queue + suffix, payload)
                    delivery = 'queue:' + queue
                mark('destination:published', task)
                store.execute("""WITH delivered AS (
                    UPDATE public.assozeta_handoff_transfers SET delivered_at=clock_timestamp(),delivery=:'delivery',
                    source_removed_at=coalesce(source_removed_at,clock_timestamp()) WHERE operation_id=:'op' AND task_id=:'task'
                    RETURNING task_id)
                    UPDATE public.assozeta_handoff_work SET source_position=NULL FROM delivered
                    WHERE assozeta_handoff_work.task_id=delivered.task_id""",
                              task=task, delivery=delivery, **values)
                delivered += 1

    def verify(record):
        values = params(record)
        require_target(values)
        problems = {r['task_id']: r['reason'] for r in blockers(record)}
        source_queue = 'assozeta-go' if values['target'] == 'django' else 'celery'
        for queue, _, _, _, raw in _items(broker, batch_size):
            if queue == source_queue:
                task, _ = _message(raw)
                problems.setdefault(task, 'running')
        rows = task_rows("""SELECT task_id FROM public.assozeta_handoff_transfers
            WHERE operation_id=:'op' AND (delivered_at IS NULL OR source_removed_at IS NULL)
            UNION SELECT task_id FROM public.assozeta_handoff_work WHERE owner_runtime=:'source'
              AND owner_generation=:'gen' AND (:'source'<>:'target' OR state='staged') AND state IN ('running','pending','staged')""", **values)
        for row in rows:
            problems.setdefault(row['task_id'], 'running')
        if problems:
            raise TransferBlocked([{'task_id': task, 'reason': reason} for task, reason in sorted(problems.items())])
        return {'outstanding_source': 0, 'undelivered': 0, 'blocked': 0}

    return {'discover': lambda record: discover(params(record)),
            'preflight': preflight, 'close_admission': close_admission, 'settle': settle,
            'stage_backlog': stage_backlog, 'activate': activate, 'verify': verify}


def abort(store, broker, record, *, barrier=None):
    values = dict(op=record['id'], source=record['previous_runtime'], gen=record['source_generation'])
    # Serialize reopening with the last held admission. Keep the operation on
    # pending rows until their source copy is restored, so a rerun can finish.
    state = store.scalar("""BEGIN;
        SELECT admission FROM public.assozeta_go_job_ownership WHERE runtime=:'source' AND generation=:'gen'
            AND (admission_operation_id=:'op' OR admission='open') FOR UPDATE;
        UPDATE public.assozeta_handoff_work SET state='pending',owner_runtime=:'source',owner_generation=:'gen'
          WHERE operation_id=:'op' AND state IN ('held','staged') AND EXISTS
            (SELECT 1 FROM public.assozeta_go_job_ownership WHERE admission_operation_id=:'op');
        UPDATE public.assozeta_handoff_work SET source_position=NULL WHERE owner_runtime=:'source' AND owner_generation=:'gen'
            AND source_position IS NOT NULL AND EXISTS (SELECT 1 FROM public.assozeta_go_job_ownership WHERE admission_operation_id=:'op');
        UPDATE public.assozeta_go_job_ownership SET admission='open',admission_operation_id=NULL
          WHERE admission_operation_id=:'op' AND runtime=:'source' AND generation=:'gen';
        COMMIT""", **values)
    if state not in ('open', 'frozen'):
        raise RuntimeError('abort requires source ownership')
    while True:
        rows = store.rows("""SELECT task_id,state,transport,queue,priority,encode(envelope,'base64') AS envelope
            FROM public.assozeta_handoff_work WHERE operation_id=:'op' AND owner_runtime=:'source'
            AND owner_generation=:'gen' AND state<>'blocked' ORDER BY accepted_at,task_id LIMIT 100""", **values)
        if not rows:
            break
        for row in rows:
            if row['transport'] == 'broker' and row['state'] == 'pending':
                broker.call('LPUSH', (row['queue'] or 'celery') + PRIORITIES[min(9,max(0,row['priority'] or 0))//3],
                            base64.b64decode(row['envelope']))
            store.execute("""UPDATE public.assozeta_handoff_work SET operation_id=NULL WHERE task_id=:'task' AND operation_id=:'op'""",
                          task=row['task_id'], **values)
    return {'admission': 'open'}


def reconcile(store, task_id, decision, actor):
    states = {'sent': 'completed', 'not_sent': 'pending', 'discard': 'cancelled'}
    if decision not in states or not actor:
        raise ValueError('invalid handoff decision')
    changed = store.scalar("""WITH resolved AS (
        UPDATE public.assozeta_handoff_work SET state=:'state',block_reason=NULL,updated_at=clock_timestamp(),
            resolved_by=:'actor',resolved_at=clock_timestamp()
        WHERE task_id=:'task' AND state='blocked' AND (:'decision'='discard' OR block_reason='ambiguous_effect')
        RETURNING task_id,transport),
        jobs AS (UPDATE public.assozeta_go_jobs j
            SET state=:'state', finished_at=CASE WHEN :'state'='pending' THEN NULL ELSE clock_timestamp() END,
                lease_owner='',lease_token='00000000-0000-0000-0000-000000000000',lease_until=NULL,error_code=''
            FROM resolved r WHERE r.transport='sql' AND j.id::text=r.task_id),
        effects AS (UPDATE public.assozeta_handoff_effects e SET
            state=CASE :'decision' WHEN 'sent' THEN 'reconciled_sent' ELSE 'reconciled_unsent' END,
            effect_key=CASE WHEN :'decision'='not_sent' THEN effect_key||':'||attempt_id::text ELSE effect_key END,
            resolved_by=:'actor',resolved_at=clock_timestamp()
            FROM resolved WHERE e.task_id=resolved.task_id AND e.state='ambiguous' AND :'decision'<>'discard')
        SELECT count(*) FROM resolved""", task=task_id, state=states[decision], decision=decision, actor=actor)
    if changed != '1':
        raise ValueError('no matching blocked job')
    return {'state': states[decision]}


def _file_barrier(directory):
    root = Path(directory)

    def mark(point, key):
        if Path(key).name != key:
            return
        with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as marker:
            json.dump({'pid': os.getpid(), 'point': point, 'key': key}, marker)
        os.replace(marker.name, root / f'arrived.{point}.{key}')
        deadline = time.monotonic() + 120
        while any((root / f'hold.{point}.{k}').exists() for k in (key, 'all')):
            if any((root / f'release.{point}.{k}').exists() for k in (key, 'all')) or time.monotonic() >= deadline:
                return
            time.sleep(0.025)
    return mark


def compose_store(root, env_file):
    from common import compose_command, compose_environment, read_env
    values = read_env(env_file)
    command = compose_command(root, env_file) + ['exec', '-T', 'postgres', 'psql',
        '-U', values.get('DBUSER', 'assozeta'), '-d', values.get('DBNAME', 'assozeta')]
    return PsqlStore(command, compose_environment(root, env_file))


class ComposeBroker:
    def __init__(self, command, environment, username, password):
        self.command, self.environment = command, environment
        self.auth = ('AUTH', username, password)

    def call(self, *arguments):
        from threading import Timer

        def send(stream, parts):
            stream.write(b'*%d\r\n' % len(parts))
            for part in parts:
                data = part if isinstance(part, bytes) else str(part).encode()
                stream.write(b'$%d\r\n' % len(data))
                stream.write(data)
                stream.write(b'\r\n')
            stream.flush()

        with subprocess.Popen(self.command, env=self.environment, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            deadline = Timer(30, process.kill)
            deadline.start()
            try:
                send(process.stdin, self.auth)
                if RespBroker._reply(process.stdout) != b'OK':
                    raise RuntimeError('broker authentication failed')
                send(process.stdin, arguments)
                result = RespBroker._reply(process.stdout)
                # Finish the pipe even when nc waits for its peer after stdin closes.
                try:
                    send(process.stdin, ('QUIT',))
                    process.stdin.close()
                except BrokenPipeError:
                    pass  # The command's complete reply has already arrived.
                process.wait()
                return result
            finally:
                deadline.cancel()
                if process.poll() is None:
                    process.kill()


def compose_broker(root, env_file):
    from common import compose_command, compose_environment, read_env
    values = read_env(env_file)
    command = compose_command(root, env_file) + ['exec', '-T', 'redis', 'nc', '127.0.0.1', '6379']
    return ComposeBroker(command, compose_environment(root, env_file),
                         values.get('REDIS_USERNAME') or 'default', values['REDIS_PASSWORD'])


class _Arguments(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes the argument value (possibly the
        # Redis authentication object or psql connection arguments).
        raise ValueError('invalid handoff arguments')


def main():
    parser = _Arguments()
    parser.add_argument('step', choices=('preflight','close-admission','settle','stage-backlog','activate','verify','abort'))
    for name in ('record','psql','redis'):
        parser.add_argument('--' + name, required=True, type=json.loads)
    parser.add_argument('--barrier-dir')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--settle-timeout', type=float, default=60)
    parser.add_argument('--freeze-timeout', type=float, default=60)
    parser.add_argument('--heartbeat-grace', type=float, default=5)
    try:
        args = parser.parse_args()
        store, broker = PsqlStore(args.psql), RespBroker(**args.redis)
        barrier = _file_barrier(args.barrier_dir) if args.barrier_dir else None
        if args.step == 'abort':
            result = abort(store, broker, args.record, barrier=barrier)
        else:
            steps = handoff_steps(store, broker, barrier=barrier, batch_size=args.batch_size,
                                  freeze_timeout=args.freeze_timeout, settle_timeout=args.settle_timeout,
                                  heartbeat_grace=args.heartbeat_grace)
            result = steps[args.step.replace('-', '_')](args.record)
    except TransferBlocked as exc:
        print(json.dumps({'blocked': exc.blockers}))
        return 3
    except Exception:
        print(json.dumps({'error': 'handoff step failed'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
