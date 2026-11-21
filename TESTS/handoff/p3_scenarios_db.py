"""Phase 3 part A scenarios: the database-only task ``reset_communication_daily_email_balance``.

Each scenario runs in the driver on a reset stack (TESTS/handoff/p3_stack.py) and raises
ContractFailure on the first unmet contract point. The contract is the docstring of
TESTS/handoff/test_p3_database_job.py.
"""
from datetime import timedelta
import uuid

import psycopg2

from TESTS.handoff import p3_oracle
from TESTS.handoff.p3_stack import DB_TASK, GO_WORKER_APP, NIL, ROOT, ContractFailure, check, wait_for

REJECTED = 'core.handoff.AdmissionRejected'


def new_id():
    return str(uuid.uuid4())


# -- shared steps -------------------------------------------------------------------------------
def consumer_of(stack, runtime, process_count=1, timeout=30):
    """The registered heartbeat row(s) of the live consumer(s) of runtime."""
    def live():
        try:
            rows = [row for row in stack.consumers(runtime) if row['heartbeat_seq'] and row['heartbeat_seq'] >= 2]
        except psycopg2.Error:
            return None
        return rows if len(rows) >= process_count else None
    stack.require_table('assozeta_handoff_consumers')
    return wait_for(live, f'no {runtime} consumer registered a heartbeat in public.assozeta_handoff_consumers '
                          '(contract: assozeta_handoff_heartbeat from every worker)', timeout=timeout)


def heartbeats_after(stack, consumer_id, count=3, timeout=20):
    first = next(r for r in stack.consumers() if r['consumer_id'] == consumer_id)['heartbeat_seq']
    wait_for(lambda: next(r for r in stack.consumers() if r['consumer_id'] == consumer_id)['heartbeat_seq'] >= first + count,
             f'consumer {consumer_id} stopped heartbeating while frozen', timeout=timeout)


def claims_since(stack, consumer_id, since):
    return [a for a in stack.attempts(consumer_id=consumer_id) if a['claimed_at'] > since]


def expect_rejected_django(stack, task_id):
    outcome = stack.django_publish(task_id)
    copies = stack.redis_copies(task_id)
    check(not copies, f'stale Django producer published {task_id} to its cached queue after the authority moved: '
                      f'{sorted(copies)}')
    check(outcome == ('raised', REJECTED),
          f'stale Django producer: apply_async must raise {REJECTED} (retryable) after commit; got {outcome}')
    check(stack.work_row(task_id) is None, f'rejected Django request {task_id} left a ledger row')


def expect_rejected_go(stack, task_id):
    result = stack.go_enqueue(task_id)
    check(stack.go_job(task_id) is None,
          f'stale Go producer was accepted after the authority moved to django (assozeta_go_jobs row {task_id}: {result})')
    check(result.get('outcome') == 'error' and 'handoff admission rejected' in result.get('error', ''),
          f'stale Go producer: Jobs.Enqueue must fail with "handoff admission rejected"; got {result}')
    check(stack.work_row(task_id) is None, f'rejected Go request {task_id} left a ledger row')


def no_effect_for_a_while(stack, consumer_id, task_id, description):
    """Positive progress evidence (heartbeats) instead of a sleep, then zero effects."""
    heartbeats_after(stack, consumer_id)
    check(stack.effect_total() == 0, f'{description}: a business effect was committed')
    completed = [a for a in stack.attempts(task_id=task_id) if a['outcome'] == 'completed']
    check(not completed, f'{description}: {task_id} has a completed attempt {completed}')


# -- schema -------------------------------------------------------------------------------------
TABLE_COLUMNS = {
    'assozeta_go_job_ownership': {'singleton', 'runtime', 'generation', 'operation_id', 'committed_at', 'admission',
                                  'admission_operation_id'},
    'assozeta_handoff_work': {'task_id', 'task_name', 'transport', 'queue', 'envelope', 'origin_runtime', 'owner_runtime',
                              'owner_generation', 'operation_id', 'state', 'accepted_at', 'eta', 'expires', 'parent_id',
                              'root_id', 'retries', 'priority', 'association_id', 'block_reason', 'updated_at'},
    'assozeta_handoff_attempts': {'attempt_id', 'task_id', 'runtime', 'generation', 'consumer_id', 'retries', 'claimed_at',
                                  'finished_at', 'outcome'},
    'assozeta_handoff_transfers': {'operation_id', 'task_id', 'source_runtime', 'source_generation', 'target_runtime',
                                   'target_generation', 'source_location', 'staged_at', 'source_removed_at',
                                   'delivered_at', 'delivery'},
    'assozeta_handoff_consumers': {'consumer_id', 'runtime', 'role', 'registered_at', 'heartbeat_at', 'heartbeat_seq',
                                   'freeze_operation_id', 'frozen_at'},
}
FUNCTIONS = (
    'assozeta_handoff_admit(text,text,text,text,text,bytea,timestamp with time zone,timestamp with time zone,'
    'timestamp with time zone)',
    'assozeta_handoff_claim(text,text,text,text,bytea)',
    'assozeta_handoff_fence(uuid)',
    'assozeta_handoff_finish(uuid,text)',
    'assozeta_handoff_heartbeat(text,text,text)',
    'assozeta_handoff_acknowledge_freeze(text,uuid)',
    'assozeta_handoff_close_admission(uuid,text,bigint)',
    'assozeta_commit_runtime_authority(uuid,text,bigint,text)',
)


def schema_contract(stack):
    migrations = ROOT / 'BE_GO/migrations'
    check((migrations / '000006_job_handoff.up.sql').is_file() and (migrations / '000006_job_handoff.down.sql').is_file(),
          'contract: BE_GO/migrations/000006_job_handoff.{up,down}.sql missing')
    check('const CurrentVersion = 6' in (migrations / 'migrations.go').read_text(),
          'contract: migrations.CurrentVersion must be 6')
    for table, columns in TABLE_COLUMNS.items():
        stack.require_table(table)
        missing = columns - stack.columns(table)
        check(not missing, f'contract: public.{table} lacks columns {sorted(missing)}')
    for signature in FUNCTIONS:
        found = stack.sql('SELECT to_regprocedure(%s) IS NOT NULL', ('public.' + signature,))[0][0]
        check(found, f'contract: function public.{signature} missing')
    check(stack.sql('SELECT admission FROM public.assozeta_go_job_ownership')[0][0] == 'open',
          'contract: admission defaults to open')


def sql_functions(stack):
    """The shared SQL implementation every runtime calls, exercised directly."""
    stack.require_table('assozeta_handoff_work')
    q = stack.sql
    a, b, c = new_id(), new_id(), new_id()
    envelope = b'{"headers": {"id": "x", "retries": 0}, "properties": {"priority": 0}}'

    def admit(runtime, task_id):
        return tuple(q('SELECT * FROM public.assozeta_handoff_admit(%s,%s,%s,%s,%s,%s,clock_timestamp(),NULL,NULL)',
                       (runtime, task_id, DB_TASK, 'broker', 'celery', psycopg2.Binary(envelope)))[0])

    check(admit('django', a) == ('accepted', 1), 'admit by the effective runtime must return (accepted, 1)')
    row = stack.work_row(a)
    check((row['state'], row['owner_runtime'], row['owner_generation'], row['origin_runtime'], row['transport'],
           bytes(row['envelope'])) == ('pending', 'django', 1, 'django', 'broker', envelope),
          f'accepted row must be pending, owned django/1, with the exact envelope: {row}')
    check(admit('django', a) == ('accepted', 1),
          're-admitting a pending task id of the same owner (a republish) must return accepted, not duplicate')
    check(stack.work_row(a)['state'] == 'pending', 'the republished row stays pending')
    check(admit('go', new_id())[0] == 'rejected', 'admit by a non-effective runtime must return rejected')
    claim = q("SELECT * FROM public.assozeta_handoff_claim('django','sql-consumer',%s,%s,NULL)", (a, DB_TASK))[0]
    check(claim[0] == 'claimed' and claim[2] == 1, f'claim of an owned pending row must succeed: {claim}')
    attempt = claim[1]
    check(q('SELECT * FROM public.assozeta_handoff_claim(%s,%s,%s,%s,NULL)', ('django', 'other', a, DB_TASK))[0][0] == 'duplicate',
          'a second claim of a running row must return duplicate')
    done = new_id()
    check(admit('django', done)[0] == 'accepted', 'admit')
    finished = q("SELECT * FROM public.assozeta_handoff_claim('django','sql-consumer',%s,%s,NULL)", (done, DB_TASK))[0]
    q("SELECT public.assozeta_handoff_finish(%s,'completed')", (finished[1],))
    check(admit('django', done)[0] == 'duplicate', 're-admitting a completed task id must return duplicate')
    operation = str(uuid.uuid4())
    check(q("SELECT public.assozeta_handoff_close_admission(%s,'django',1)", (operation,))[0][0] == 'closed',
          'close_admission must return closed')
    check(q("SELECT public.assozeta_handoff_close_admission(%s,'django',1)", (operation,))[0][0] == 'already_closed',
          'repeating close_admission must return already_closed')
    check(admit('django', b) == ('held', 1), 'admission while frozen must return (held, source generation)')
    check(stack.work_row(b)['state'] == 'held' and str(stack.work_row(b)['operation_id']) == operation,
          'a held row records the freezing operation')
    check(q("SELECT * FROM public.assozeta_handoff_claim('django','sql-consumer',%s,%s,NULL)", (b, DB_TASK))[0][0] == 'frozen',
          'claims while admission is frozen must return frozen')
    beat = q("SELECT * FROM public.assozeta_handoff_heartbeat('sql-consumer','django','broker')")[0]
    check(tuple(beat) == ('django', 1, 'frozen', uuid.UUID(operation)), f'heartbeat reports the freeze: {beat}')
    check(q("SELECT public.assozeta_handoff_acknowledge_freeze('sql-consumer',%s)", (operation,))[0][0] == 'acknowledged',
          'acknowledge_freeze must return acknowledged')
    consumer = next(r for r in stack.consumers() if r['consumer_id'] == 'sql-consumer')
    check(str(consumer['freeze_operation_id']) == operation and consumer['frozen_at'] is not None, f'ack recorded: {consumer}')
    q('SELECT public.assozeta_handoff_fence(%s)', (attempt,))
    commit = stack.commit_authority(operation, 'django', 1, 'go')
    check(tuple(commit) == ('committed', 'go', 2), f'commit: {commit}')
    check(stack.sql('SELECT admission, admission_operation_id FROM public.assozeta_go_job_ownership')[0] == ('open', None),
          'commit reopens admission for the target')
    held = stack.work_row(b)
    check((held['state'], held['owner_runtime'], held['owner_generation']) == ('pending', 'go', 2),
          f'commit binds held work of the operation to the target generation: {held}')
    for statement in ('SELECT public.assozeta_handoff_fence(%s)', "SELECT public.assozeta_handoff_finish(%s,'completed')"):
        try:
            q(statement, (attempt,))
        except psycopg2.Error as exc:
            check(exc.pgcode == 'AZF01', f'a stale attempt must be refused with SQLSTATE AZF01; got {exc.pgcode}')
        else:
            raise ContractFailure(f'{statement.split("(")[0]} accepted an attempt of the previous generation')
    check(q("SELECT * FROM public.assozeta_handoff_claim('django','sql-consumer',%s,%s,NULL)", (b, DB_TASK))[0][0] == 'not_owner',
          'a claim by the previous runtime must return not_owner')
    check(admit('django', c)[0] == 'rejected', 'the previous runtime cannot admit after commit')


# -- Django -> Go -------------------------------------------------------------------------------
def forward_stale_producer(stack):
    stack.commit_authority(str(uuid.uuid4()), 'django', 1, 'go')
    expect_rejected_django(stack, new_id())


def forward_stale_worker_claim(stack):
    stack.seed_balance()
    task = new_id()
    check(stack.django_publish(task) == ('returned', None), 'pre-commit Django publication must be accepted')
    stack.commit_authority(str(uuid.uuid4()), 'django', 1, 'go')
    worker = stack.django_worker()
    stack.django_ready()
    def evidence():
        if stack.effect_total():
            raise ContractFailure(f'stale Django worker executed {task} after the authority moved to go')
        try:
            return consumer_of(stack, 'django', timeout=0.01)
        except ContractFailure:
            return None
        except psycopg2.Error:
            return None
    wait_for(evidence, 'stale Django worker never registered a heartbeat (contract: assozeta_handoff_heartbeat)',
             timeout=30)
    consumer = consumer_of(stack, 'django')[0]
    no_effect_for_a_while(stack, consumer['consumer_id'], task, 'stale Django worker after commit')
    check(stack.task_result(task) != 'SUCCESS', f'stale Django worker reported {task} SUCCESS after commit')
    stack.stop(worker)


def forward_stale_worker_complete(stack):
    stack.seed_balance()
    worker = stack.django_worker()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(stack.django_publish(task) == ('returned', None), 'Django publication must be accepted')
    stack.barrier.wait_arrived('claim:granted', task, 'Django worker')
    stack.commit_authority(str(uuid.uuid4()), 'django', 1, 'go')
    stack.barrier.release('claim:granted', task)
    stack.barrier.wait_arrived('attempt:fenced', task, 'Django worker (stale completion)')
    check(stack.effect_total() == 0, 'a Django attempt of the previous generation committed its business effect')
    attempt, = stack.attempts(task_id=task)
    check(attempt['outcome'] == 'fenced' and (attempt['runtime'], attempt['generation']) == ('django', 1),
          f'the stale attempt must be recorded fenced: {attempt}')
    check(stack.work_row(task)['state'] != 'completed', 'a fenced attempt completed its job')
    stack.stop(worker)


def forward_d01(stack):
    """D01 Django -> Go: before freeze, during frozen intake, after commit from a stale process."""
    stack.job_handoff()
    stack.seed_balance()
    worker = stack.django_worker()
    before = new_id()
    check(stack.django_publish(before) == ('returned', None), 'pre-freeze publication must be accepted')
    wait_for(lambda: stack.task_result(before) == 'SUCCESS', 'pre-freeze job completes on Django')
    consumer = consumer_of(stack, 'django')[0]['consumer_id']
    reserved = new_id()
    stack.barrier.hold('reservation:confirmed', reserved)
    check(stack.django_publish(reserved) == ('returned', None), 'pre-freeze publication must be accepted')
    stack.barrier.wait_arrived('reservation:confirmed', reserved, 'Django worker')
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps()
    closed = steps['close_admission'](record)
    row = next(r for r in stack.consumers('django') if r['consumer_id'] == consumer)
    check(str(row['freeze_operation_id']) == str(operation) and row['frozen_at'] is not None,
          f'close_admission returned before the Django worker acknowledged the freeze: {row}; summary {closed}')
    frozen_at = row['frozen_at']
    stack.barrier.release('reservation:confirmed', reserved)
    stack.barrier.wait_arrived('claim:refused', reserved, 'Django worker (claim during freeze)')
    held = new_id()
    check(stack.django_publish(held) == ('returned', None), 'a publication during frozen intake must succeed (held)')
    row = stack.work_row(held)
    check(row and row['state'] == 'held' and not stack.redis_copies(held),
          f'frozen intake must hold the job durably without publishing it: {row}, {stack.redis_copies(held)}')
    duplicate = bytes(stack.work_row(reserved)['envelope'])
    stack.redis.lpush('celery', duplicate)
    heartbeats_after(stack, consumer)
    # An in-flight blocking pop may hand the copy back (kombu requeues it), but the frozen worker
    # must neither claim it nor drop it: the broker still holds it, by id, for staging.
    copies = stack.redis_copies(reserved)
    check(copies and set(copies) <= {'celery', 'unacked'},
          f'the frozen Django worker consumed a broker copy after its freeze acknowledgement: {sorted(copies)}')
    check(not claims_since(stack, consumer, frozen_at), 'the Django worker claimed work after its freeze acknowledgement')
    steps['settle'](record)
    stack.stop(worker)
    staged = steps['stage_backlog'](record)
    check(not stack.redis_copies(reserved), f'staging must remove the source broker copy of {reserved}: {staged}')
    transfer = stack.sql('SELECT source_location, source_removed_at IS NOT NULL FROM public.assozeta_handoff_transfers '
                         'WHERE operation_id=%s AND task_id=%s', (str(operation), reserved))
    check(transfer == [('queue', True)], f'staging must record a transfer receipt (queue, source removed): {transfer}')
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'authority commit')
    stale = new_id()
    expect_rejected_django(stack, stale)
    steps['activate'](record)
    for task in (reserved, held):
        job = stack.go_job(task)
        check(job is not None and job[0] == 'pending' and job[5] == DB_TASK,
              f'activation must deliver {task} as a pending Go durable job with the same id: {job}')
        accepted_at = stack.work_row(task)['accepted_at']
        check(job[6] == accepted_at, f'{task}: the Go row keeps the original acceptance stamp ({job[6]} != {accepted_at})')
    go = stack.go_worker()
    for task in (reserved, held):
        wait_for(lambda: (stack.go_job(task) or ('',))[0] == 'completed', f'{task} runs on the Go durable worker')
    after = new_id()
    result = stack.go_enqueue(after)
    check(result.get('outcome') == 'enqueued', f'a post-commit Go producer must be accepted: {result}')
    wait_for(lambda: (stack.go_job(after) or ('',))[0] == 'completed', 'post-commit Go job runs on Go')
    stack.stop(go)
    p3_oracle.check(stack, accepted={before: 'source_completed', reserved: 'target_completed', held: 'target_completed',
                                     after: 'target_completed'},
                    rejected=[stale], source=('django', 1), target=('go', 2), effects={'django': 1, 'go': 3})


# -- Go -> Django -------------------------------------------------------------------------------
def to_go(stack):
    stack.commit_authority(str(uuid.uuid4()), 'django', 1, 'go')


def reverse_stale_producer(stack):
    to_go(stack)
    stack.commit_authority(str(uuid.uuid4()), 'go', 2, 'django')
    expect_rejected_go(stack, new_id())


def reverse_stale_worker_complete(stack):
    to_go(stack)
    stack.seed_balance()
    worker = stack.go_worker()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    result = stack.go_enqueue(task)
    check(result.get('outcome') == 'enqueued', f'Go enqueue while Go owns: {result}')
    stack.barrier.wait_arrived('claim:granted', task, 'Go durable worker')
    stack.commit_authority(str(uuid.uuid4()), 'go', 2, 'django')
    stack.barrier.release('claim:granted', task)
    stack.barrier.wait_arrived('attempt:fenced', task, 'Go durable worker (stale completion)')
    check(stack.effect_total() == 0, 'a Go attempt of the previous generation committed its business effect')
    check(stack.go_job(task)[0] != 'completed', 'a fenced Go attempt completed its job')
    attempt, = stack.attempts(task_id=task)
    check(attempt['outcome'] == 'fenced' and (attempt['runtime'], attempt['generation']) == ('go', 2),
          f'the stale Go attempt must be recorded fenced: {attempt}')
    stack.stop(worker)


def reverse_d01(stack):
    """D01 Go -> Django, starting from Go-created durable rows with no Redis envelope."""
    stack.job_handoff()
    to_go(stack)
    stack.seed_balance()
    worker = stack.go_worker()
    before = new_id()
    check(stack.go_enqueue(before).get('outcome') == 'enqueued', 'pre-freeze Go enqueue must be accepted')
    wait_for(lambda: (stack.go_job(before) or ('',))[0] == 'completed', 'pre-freeze Go job completes on Go')
    consumer = consumer_of(stack, 'go')[0]['consumer_id']
    future = new_id()
    due = (stack.now() + timedelta(days=30)).replace(microsecond=0)
    check(stack.go_enqueue(future, available_at=due).get('outcome') == 'enqueued', 'future Go job must be accepted')
    check(not stack.redis.keys('*'), 'a Go durable job must have no broker envelope')
    operation = uuid.uuid4()
    record = stack.record(operation, 'go', 2, 'django')
    steps = stack.handoff_steps()
    closed = steps['close_admission'](record)
    row = next(r for r in stack.consumers('go') if r['consumer_id'] == consumer)
    check(str(row['freeze_operation_id']) == str(operation) and row['frozen_at'] is not None,
          f'close_admission returned before the Go worker acknowledged the freeze: {row}; summary {closed}')
    frozen_at = row['frozen_at']
    held = new_id()
    check(stack.go_enqueue(held).get('outcome') == 'enqueued', 'a Go enqueue during frozen intake must succeed (held)')
    check(stack.work_row(held)['state'] == 'held', 'frozen intake must record the Go job as held')
    heartbeats_after(stack, consumer)
    job = stack.go_job(held)
    check(job[0] == 'pending' and job[1] == 0 and job[2] == '',
          f'the frozen Go worker claimed a held job after its freeze acknowledgement: {job}')
    check(not claims_since(stack, consumer, frozen_at), 'the Go worker claimed work after its freeze acknowledgement')
    steps['settle'](record)
    stack.stop(worker)
    steps['stage_backlog'](record)
    transfer = stack.sql('SELECT source_location FROM public.assozeta_handoff_transfers WHERE operation_id=%s AND task_id=%s',
                         (str(operation), future))
    check(transfer == [('sql',)], f'staging must record a transfer receipt for the pending Go row: {transfer}')
    check(tuple(stack.commit_authority(str(operation), 'go', 2, 'django')) == ('committed', 'django', 3), 'authority commit')
    stale = new_id()
    expect_rejected_go(stack, stale)
    steps['activate'](record)
    import base64
    import json
    queued = {}
    for raw in stack.redis.lrange('celery', 0, -1):
        message = json.loads(raw)
        queued[message['headers']['id']] = message
    check(set(queued) == {future, held}, f'activation must publish exactly the transferred Go jobs to celery: {sorted(queued)}')
    for task in (future, held):
        message = queued[task]
        headers = message['headers']
        work = stack.work_row(task)
        check(headers['task'] == DB_TASK and headers['root_id'] == task and headers['parent_id'] is None
              and headers['retries'] == 0 and message['properties']['delivery_info']['routing_key'] == 'celery',
              f'{task}: Celery protocol 2 envelope with the same id, task name and lineage: {headers}')
        check(abs(headers['assozeta_published_at'] - work['accepted_at'].timestamp()) < 1e-3,
              f'{task}: assozeta_published_at must be the original acceptance stamp')
        check(json.loads(base64.b64decode(message['body']))[:2] == [[], {}], f'{task}: empty args/kwargs')
        job = stack.go_job(task)
        check((job[0], job[4]) == ('cancelled', 'handoff_transferred'),
              f'{task}: the transferred Go row must be cancelled with error_code handoff_transferred: {job}')
    from datetime import datetime
    eta = queued[future]['headers']['eta']
    check(eta and datetime.fromisoformat(eta) == due, f'the future job keeps its due time as the Celery eta: {eta} != {due}')
    check(queued[held]['headers']['eta'] is None, 'a due job is published without an eta')
    django = stack.django_worker()
    wait_for(lambda: stack.task_result(held) == 'SUCCESS', 'the held Go job runs once on Django with the same id')
    after = new_id()
    check(stack.django_publish(after) == ('returned', None), 'post-commit Django publication must be accepted')
    wait_for(lambda: stack.task_result(after) == 'SUCCESS', 'post-commit Django job runs on Django')
    heartbeats_after(stack, consumer_of(stack, 'django')[0]['consumer_id'])
    check(stack.task_result(future) is None, 'the month-ahead job became due on Django after the transfer')
    stack.stop(django)
    p3_oracle.check(stack, accepted={before: 'source_completed', future: 'target_pending', held: 'target_completed',
                                     after: 'target_completed'},
                    rejected=[stale], source=('go', 2), target=('django', 3), effects={'go': 1, 'django': 2})


# -- behaviour beyond the happy path (added in verification round 1) -------------------------------
def retry_is_republished_while_open(stack):
    """A Celery retry republishes the same task id; admission must not swallow it as a duplicate."""
    import json
    from TESTS.handoff.p3_scenarios_email import publish_mail
    stack.require_table('assozeta_handoff_work')
    worker = stack.django_worker()
    stack.django_ready()
    stack.smtp.failing = True
    task = new_id()
    check(publish_mail(stack, task) == ('returned', None), 'Django publication')

    def retried():
        for raw in [*stack.redis.lrange('celery', 0, -1), *stack.redis.hvals('unacked')]:
            text = raw.decode()
            if task in text and '"retries": 1' in text:
                return True
        return False
    wait_for(lambda: retried() or (stack.work_row(task) or {}).get('retries') == 1 and stack.task_result(task) == 'RETRY',
             f'the failed send of {task} was never retried')
    wait_for(retried, f'the retry of {task} (retries 1) was admitted but never published: the job is lost until a switch '
                      '(a republish of an existing task id must not be dropped as a duplicate)', timeout=10)
    row = stack.work_row(task)
    check(row['state'] in ('pending', 'running') and row['retries'] == 1,
          f'the ledger records the retry in place: state {row["state"]}, retries {row["retries"]}')
    stack.stop(worker)
    assert json


def rollback_publishes_nothing_commit_publishes_once(stack):
    """apply_async inside a business transaction: nothing before commit, nothing after a rollback."""
    from django.db import transaction
    from application.tasks import reset_email_balance
    stack.require_table('assozeta_handoff_work')
    rolled, committed = new_id(), new_id()

    class Abort(Exception):
        pass
    try:
        with transaction.atomic():
            reset_email_balance.apply_async(task_id=rolled)
            raise Abort()
    except Abort:
        pass
    check(stack.work_row(rolled) is None and not stack.redis_copies(rolled),
          f'a publication inside a rolled-back transaction left work behind: {stack.work_row(rolled)}, '
          f'{sorted(stack.redis_copies(rolled))}')
    with transaction.atomic():
        reset_email_balance.apply_async(task_id=committed)
        check(not stack.redis_copies(committed), 'a publication inside a transaction reached the broker before commit')
    check(list(stack.redis_copies(committed)) == ['celery'] and len(stack.redis_copies(committed)['celery']) == 1,
          f'the committed publication is pushed exactly once after commit: {stack.redis_copies(committed)}')
    check(stack.work_row(committed)['state'] == 'pending', 'and its ledger row is pending')


def admission_racing_close_is_staged(stack):
    """A producer whose admitting transaction is still open when the freeze starts: the freeze waits, staging sees it."""
    import threading
    from django.db import transaction
    from application.tasks import reset_email_balance
    stack.job_handoff()
    stack.seed_balance()
    task = new_id()
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    result = {}
    with transaction.atomic():
        reset_email_balance.apply_async(task_id=task)
        closing = threading.Thread(target=lambda: result.setdefault('close', steps['close_admission'](record)), daemon=True)
        closing.start()
        wait_for(lambda: stack.sql("""SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'
            AND query LIKE '%%assozeta_handoff_close_admission%%'""")[0][0] == 1,
                 'close_admission must wait for the open admitting transaction (it took no exclusive authority lock)')
        check(closing.is_alive(), 'close_admission returned while an admitted publication was uncommitted')
    closing.join(30)
    check('close' in result, 'close_admission did not finish after the producer committed')
    steps['settle'](record)
    steps['stage_backlog'](record)
    check(stack.work_row(task)['state'] == 'staged', f'the racing job is staged: {stack.work_row(task)["state"]}')
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    steps['activate'](record)
    go = stack.go_worker()
    wait_for(lambda: (stack.go_job(task) or ('',))[0] == 'completed', 'the racing job runs once on Go')
    stack.stop(go)
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 1})


def frozen_go_worker_adopts_nothing(stack):
    """A pre-ledger Go row appearing during the freeze is not adopted or claimed by the frozen worker."""
    stack.job_handoff()
    to_go(stack)
    stack.seed_balance()
    worker = stack.go_worker()
    consumer = consumer_of(stack, 'go')[0]['consumer_id']
    operation = uuid.uuid4()
    record = stack.record(operation, 'go', 2, 'django')
    stack.handoff_steps(heartbeat_grace=1.0)['close_admission'](record)
    historical = new_id()
    stack.sql("""INSERT INTO public.assozeta_go_jobs (id, association_id, kind, version, idempotency_key, payload, max_attempts,
      available_at) VALUES (%s, %s, %s, 1, %s, '{}', 3, clock_timestamp())""", (historical, NIL, DB_TASK, 'historical:' + historical))
    heartbeats_after(stack, consumer)
    job = stack.go_job(historical)
    check(job[0] == 'pending' and job[1] == 0 and stack.work_row(historical) is None and stack.effect_total() == 0,
          f'the frozen Go worker adopted or claimed a pre-ledger row: {job}, ledger {stack.work_row(historical)}')
    stack.stop(worker)


def legacy_without_ledger_is_unchanged(stack):
    """A database before migration 6 (no ledger table, schema version 5): both runtimes publish and execute as before."""
    stack.seed_balance()
    stack.sql('ALTER TABLE public.assozeta_handoff_work RENAME TO assozeta_handoff_work_hidden')
    stack.sql('UPDATE public.assozeta_go_schema_migrations SET version = version - 1')
    try:
        task = new_id()
        check(stack.django_publish(task) == ('returned', None), 'legacy Django publication must succeed')
        check(list(stack.redis_copies(task)) == ['celery'], 'legacy publication reaches the celery queue')
        worker = stack.django_worker()
        wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the legacy Django worker executes the job')
        stack.stop(worker)
        stack.set_authority('go', 2)  # fixture: the commit function itself needs the ledger table
        job = new_id()
        check(stack.go_enqueue(job).get('outcome') == 'enqueued', 'legacy Go enqueue must succeed')
        go = stack.go_worker()
        wait_for(lambda: (stack.go_job(job) or ('',))[0] == 'completed', 'the legacy Go worker executes the job')
        stack.stop(go)
        check(stack.effects() == {'handoff-django-worker': 1, 'assozeta-go-worker': 1}, f'one effect each: {stack.effects()}')
    finally:
        stack.sql('UPDATE public.assozeta_go_schema_migrations SET version = version + 1')
        stack.sql('ALTER TABLE public.assozeta_handoff_work_hidden RENAME TO assozeta_handoff_work')


SCENARIOS = {
    'schema_contract': schema_contract,
    'sql_functions': sql_functions,
    'forward_stale_producer': forward_stale_producer,
    'forward_stale_worker_claim': forward_stale_worker_claim,
    'forward_stale_worker_complete': forward_stale_worker_complete,
    'forward_d01': forward_d01,
    'reverse_stale_producer': reverse_stale_producer,
    'reverse_stale_worker_complete': reverse_stale_worker_complete,
    'reverse_d01': reverse_d01,
    'retry_is_republished_while_open': retry_is_republished_while_open,
    'rollback_publishes_nothing_commit_publishes_once': rollback_publishes_nothing_commit_publishes_once,
    'admission_racing_close_is_staged': admission_racing_close_is_staged,
    'frozen_go_worker_adopts_nothing': frozen_go_worker_adopts_nothing,
    'legacy_without_ledger_is_unchanged': legacy_without_ledger_is_unchanged,
}
assert GO_WORKER_APP
