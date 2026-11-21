"""Controller steps for transferring the instance-wide database reset job."""
import base64
import json
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone

RESET_TASK = 'reset_communication_daily_email_balance'
PRIORITIES = ('', '\x06\x163', '\x06\x166', '\x06\x169')


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


def _copies(broker, queue, batch_size, task_ids):
    """Find only the selected ledger jobs in Kombu's priority and unacked stores."""
    found = {task: [] for task in task_ids}
    for suffix in PRIORITIES:
        key = queue + suffix
        start = 0
        while True:
            page = broker.call('LRANGE', key, start, start + batch_size - 1)
            for raw in page:
                task = json.loads(raw).get('headers', {}).get('id')
                if task in found:
                    found[task].append(('queue', key, raw))
            if len(page) < batch_size:
                break
            start += batch_size
    cursor = b'0'
    while True:
        cursor, pairs = broker.call('HSCAN', 'unacked', cursor, 'COUNT', batch_size)
        for tag, raw in zip(pairs[::2], pairs[1::2]):
            message, _, routing_key = json.loads(raw)
            task = message.get('headers', {}).get('id')
            if routing_key == queue and task in found:
                found[task].append(('unacked', tag, raw))
        if cursor == b'0':
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
    body = [[], {}, {'callbacks': None, 'errbacks': None, 'chain': None, 'chord': None}]
    return json.dumps({
        'body': base64.b64encode(json.dumps(body).encode()).decode(),
        'content-encoding': 'utf-8', 'content-type': 'application/json',
        'headers': {'lang': 'py', 'task': RESET_TASK, 'id': task, 'eta': eta, 'expires': None,
                    'retries': job['attempts'], 'root_id': task, 'parent_id': None, 'timelimit': [None, None],
                    'assozeta_published_at': datetime.fromisoformat(job['created_at']).timestamp()},
        'properties': {'correlation_id': task, 'delivery_mode': 2, 'delivery_tag': str(uuid.uuid4()),
                       'body_encoding': 'base64', 'priority': 0,
                       'delivery_info': {'exchange': '', 'routing_key': 'celery'}},
    }).encode()


def handoff_steps(store, broker, *, barrier=None, batch_size=500, freeze_timeout=60.0, settle_timeout=60.0,
                  heartbeat_grace=5.0):
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    mark = barrier or (lambda point, key: None)

    def params(record):
        return dict(op=record['id'], source=record['previous_runtime'], gen=record['source_generation'],
                    target=record['target'])

    def close_admission(record):
        values = params(record)
        result = store.scalar("SELECT public.assozeta_handoff_close_admission(:'op', :'source', :'gen')", **values)
        if result not in ('closed', 'already_closed'):
            raise RuntimeError('handoff admission conflict')
        deadline = time.monotonic() + freeze_timeout
        observed = set()
        while True:
            live = store.rows("""SELECT consumer_id, freeze_operation_id::text AS op FROM public.assozeta_handoff_consumers
                WHERE runtime=:'source' AND (freeze_operation_id=:'op' OR
                      heartbeat_at >= clock_timestamp() - make_interval(secs => :'grace'))""",
                              grace=heartbeat_grace, **values)
            observed.update(row['consumer_id'] for row in live)
            acknowledged = {row['consumer_id'] for row in live if row['op'] == str(values['op'])}
            if observed == acknowledged:
                return {'acknowledged': len(acknowledged)}
            if time.monotonic() >= deadline:
                raise RuntimeError('source consumers have not acknowledged the freeze')
            time.sleep(0.05)

    def settle(record):
        deadline = time.monotonic() + settle_timeout
        while True:
            running = int(store.scalar("""SELECT count(*) FROM public.assozeta_handoff_attempts
                WHERE runtime=:'source' AND generation=:'gen' AND outcome='running'""", **params(record)))
            if not running:
                return {'running': 0}
            if time.monotonic() >= deadline:
                raise RuntimeError(f'{running} source attempts still running')
            time.sleep(0.05)

    def stage_backlog(record):
        values = params(record)
        frozen = store.scalar("""SELECT count(*) FROM public.assozeta_go_job_ownership
            WHERE runtime=:'source' AND generation=:'gen' AND admission_operation_id=:'op'""", **values)
        if frozen != '1':
            raise RuntimeError('source admission must be frozen before staging')
        queue = 'celery' if values['source'] == 'django' else 'assozeta-go'
        staged = 0
        while True:
            rows = store.rows("""SELECT w.task_id, w.task_name, w.transport, w.state FROM public.assozeta_handoff_work w
                LEFT JOIN public.assozeta_handoff_transfers t ON t.operation_id=:'op' AND t.task_id=w.task_id
                WHERE w.owner_runtime=:'source' AND w.owner_generation=:'gen'
                  AND (w.state='pending' OR (w.state='staged' AND t.source_removed_at IS NULL AND w.transport='broker'))
                ORDER BY w.accepted_at,w.task_id LIMIT :'batch'""", batch=batch_size, **values)
            if not rows:
                return {'staged': staged}
            ids = [r['task_id'] for r in rows if r['transport'] == 'broker']
            copies = _copies(broker, queue, batch_size, ids) if ids else {}
            for row in rows:
                task = row['task_id']
                if row['task_name'] != RESET_TASK:
                    raise RuntimeError('task has no database handoff adapter')
                locations = copies.get(task, [])
                location = 'sql' if row['transport'] == 'sql' else locations[0][0] if locations else 'ledger'
                if row['state'] == 'pending':
                    store.execute("""WITH staged AS (
                        UPDATE public.assozeta_handoff_work SET state='staged',operation_id=:'op',updated_at=clock_timestamp()
                        WHERE task_id=:'task' AND state='pending' AND owner_runtime=:'source' AND owner_generation=:'gen'
                        RETURNING task_id)
                        INSERT INTO public.assozeta_handoff_transfers
                        (operation_id,task_id,source_runtime,source_generation,target_runtime,source_location)
                        SELECT :'op',task_id,:'source',:'gen',:'target',:'location' FROM staged""",
                                  task=task, location=location, **values)
                    mark('receipt:persisted', task)
                if row['transport'] == 'broker':
                    _remove(broker, locations)
                    store.execute("""UPDATE public.assozeta_handoff_transfers SET source_removed_at=clock_timestamp()
                        WHERE operation_id=:'op' AND task_id=:'task'""", task=task, **values)
                    mark('source:acked', task)
                staged += 1

    def activate(record):
        values = params(record)
        committed = store.scalar("""SELECT count(*) FROM public.assozeta_go_job_ownership WHERE runtime=:'target'
            AND generation=:'gen'::bigint+1 AND operation_id=:'op'""", **values)
        if committed != '1':
            raise RuntimeError('target ownership must commit before activation')
        delivered = 0
        while True:
            rows = store.rows("""SELECT w.task_id,w.task_name,w.transport,w.accepted_at,w.eta,w.retries,
                       j.attempts,j.created_at,j.available_at
                FROM public.assozeta_handoff_transfers t JOIN public.assozeta_handoff_work w USING(task_id)
                LEFT JOIN public.assozeta_go_jobs j ON j.id::text=w.task_id
                WHERE t.operation_id=:'op' AND t.delivered_at IS NULL AND w.owner_runtime=:'target'
                  AND w.owner_generation=t.target_generation ORDER BY w.accepted_at,w.task_id LIMIT :'batch'""",
                              batch=batch_size, **values)
            if not rows:
                return {'delivered': delivered}
            for row in rows:
                task = row['task_id']
                if row['task_name'] != RESET_TASK:
                    raise RuntimeError('task has no database handoff adapter')
                if values['target'] == 'go':
                    store.execute("""BEGIN;
                        INSERT INTO public.assozeta_go_jobs (id,association_id,kind,version,idempotency_key,payload,
                          max_attempts,attempts,available_at,created_at)
                        SELECT task_id::uuid,'00000000-0000-0000-0000-000000000000',task_name,1,'handoff:'||task_id,
                            '{}',3,retries,coalesce(eta,accepted_at),accepted_at FROM public.assozeta_handoff_work
                          WHERE task_id=:'task'
                        ON CONFLICT (id) DO UPDATE SET state='pending',error_code='',finished_at=NULL,
                          attempts=excluded.attempts,available_at=excluded.available_at
                          WHERE assozeta_go_jobs.state='cancelled' AND assozeta_go_jobs.error_code='handoff_transferred';
                        UPDATE public.assozeta_handoff_work SET transport='sql',queue=NULL,envelope=NULL,
                          updated_at=clock_timestamp() WHERE task_id=:'task';
                        COMMIT""", task=task)
                    delivery = 'sql:' + task
                else:
                    if row['created_at'] is None:
                        raise RuntimeError('source durable job missing')
                    payload = _envelope(row)
                    broker.call('LPUSH', 'celery', payload)
                    store.execute("""BEGIN;
                        UPDATE public.assozeta_go_jobs SET state='cancelled',error_code='handoff_transferred',
                          finished_at=clock_timestamp() WHERE id=:'task'::uuid AND state='pending';
                        UPDATE public.assozeta_handoff_work SET transport='broker',queue='celery',
                          envelope=decode(:'payload','base64'),updated_at=clock_timestamp() WHERE task_id=:'task';
                        COMMIT""", task=task, payload=base64.b64encode(payload).decode())
                    delivery = 'queue:celery'
                mark('destination:published', task)
                store.execute("""UPDATE public.assozeta_handoff_transfers SET delivered_at=clock_timestamp(),delivery=:'delivery',
                    source_removed_at=coalesce(source_removed_at,clock_timestamp()) WHERE operation_id=:'op' AND task_id=:'task'""",
                              task=task, delivery=delivery, **values)
                delivered += 1

    return {'close_admission': close_admission, 'settle': settle, 'stage_backlog': stage_backlog, 'activate': activate}
