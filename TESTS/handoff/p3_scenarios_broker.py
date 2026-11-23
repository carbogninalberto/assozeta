"""Phase 3 part B: the Redis backlog (D02, D03, D04, D06, D10, D15). Contract: test_p3_email_job.py docstring."""
from datetime import datetime, timedelta, timezone
import json
import signal
import time
import uuid

from TESTS.handoff import p3_oracle
from TESTS.handoff.p3_scenarios_db import consumer_of, heartbeats_after, new_id, to_go
from TESTS.handoff.p3_scenarios_email import (blocked, go_consumer_of, go_mail, handoff, mail_kwargs, publish_mail,
                                              reconcile)
from TESTS.handoff.p3_stack import (DB_TASK, GO_QUEUE, MAIL_TASK, PRIORITY_SEPARATOR, ContractFailure, check,
                                    wait_for)

PRIORITIES = (0, 3, 6, 9)
UNRELATED = ('unrelated-queue', 'unrelated:cache', 'unrelated:hash')


def seed_unrelated(stack):
    stack.redis.lpush('unrelated-queue', stack.envelope('unrelated.task', new_id(), queue='unrelated-queue', stamp=1.0),
                      stack.envelope('unrelated.task', new_id(), queue='unrelated-queue', stamp=2.0))
    stack.redis.set('unrelated:cache', b'\x00binary\xffvalue')
    stack.redis.hset('unrelated:hash', mapping={'a': '1', 'b': '2'})
    return {k: v for k, v in stack.broker_snapshot().items() if k in UNRELATED}


def subqueues(stack, queue):
    """{redis key: [task ids, oldest first]} for a queue and its priority subqueues (BRPOP takes the tail)."""
    keys = [queue] + [f'{queue}{PRIORITY_SEPARATOR}{p}' for p in (3, 6, 9)]
    return {key: list(reversed(stack.queued_ids(key))) for key in keys if stack.redis.llen(key)}


def preserves_order(before, after):
    """Every source list's relative order survives in the destination lists."""
    position = {task: (key, index) for key, ids in after.items() for index, task in enumerate(ids)}
    for ids in before.values():
        placed = [position.get(task) for task in ids]
        if None in placed:
            return False
        for (key_a, a), (key_b, b) in zip(placed, placed[1:]):
            if key_a == key_b and a > b:
                return False
    return True


# -- D02 ----------------------------------------------------------------------------------------------
def d02_priorities_forward(stack):
    stack.job_handoff()
    accepted = {}
    for priority in PRIORITIES:
        for queue in ('celery', GO_QUEUE):
            for _ in range(2):
                task = new_id()
                check(publish_mail(stack, task, queue=queue, priority=priority) == ('returned', None), 'Django publication')
                accepted[task] = priority
    unrelated = seed_unrelated(stack)
    before = {**subqueues(stack, 'celery'), **subqueues(stack, GO_QUEUE)}
    check(sum(map(len, before.values())) == len(accepted), f'fixture: every job queued once {before}')
    handoff(stack, 'django', 1, 'go')
    after = subqueues(stack, GO_QUEUE)
    found = [task for ids in after.values() for task in ids]
    check(sorted(found) == sorted(accepted) and len(found) == len(set(found)),
          f'the Go queue holds exactly the accepted ids once (by identity, not count): {len(found)} of {len(accepted)}')
    for key, ids in after.items():
        for task in ids:
            check(stack.priority_queue(GO_QUEUE, accepted[task]) == key,
                  f'{task} (priority {accepted[task]}) delivered to {key!r}')
    check(preserves_order(before, after), 'FIFO order within each source priority subqueue is preserved')
    check(not subqueues(stack, 'celery'), 'no copy is left on the Django queue')
    check({k: v for k, v in stack.broker_snapshot().items() if k in UNRELATED} == unrelated, 'unrelated keys changed')
    consumer = stack.go_consumer()
    wait_for(lambda: all(stack.task_result(t) == 'SUCCESS' for t in accepted), 'every job runs on the Go consumer', timeout=90)
    stack.stop(consumer)
    check(stack.smtp.count() == len(accepted), f'SMTP accepted {stack.smtp.count()} for {len(accepted)} jobs')
    p3_oracle.check(stack, accepted={t: 'target_completed' for t in accepted}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 0}, completions={'go': len(accepted)})


def d02_priorities_reverse(stack):
    stack.job_handoff()
    to_go(stack)
    accepted = {}
    for priority in PRIORITIES:
        for queue in ('celery', GO_QUEUE):
            for _ in range(2):
                task = new_id()
                go_mail(stack, task, queue=queue, priority=priority)
                accepted[task] = priority
    unrelated = seed_unrelated(stack)
    before = {**subqueues(stack, 'celery'), **subqueues(stack, GO_QUEUE)}
    handoff(stack, 'go', 2, 'django')
    after = subqueues(stack, 'celery')
    found = [task for ids in after.values() for task in ids]
    check(sorted(found) == sorted(accepted) and len(found) == len(set(found)),
          f'the Django queue holds exactly the accepted ids once: {len(found)} of {len(accepted)}')
    for key, ids in after.items():
        for task in ids:
            check(stack.priority_queue('celery', accepted[task]) == key, f'{task} delivered to {key!r}')
    check(preserves_order(before, after), 'FIFO order within each source priority subqueue is preserved')
    check(not subqueues(stack, GO_QUEUE), 'no copy is left on the Go queue')
    check({k: v for k, v in stack.broker_snapshot().items() if k in UNRELATED} == unrelated, 'unrelated keys changed')
    worker = stack.django_worker()
    wait_for(lambda: all(stack.task_result(t) == 'SUCCESS' for t in accepted), 'every job runs on Django', timeout=90)
    stack.stop(worker)
    check(stack.smtp.count() == len(accepted), f'SMTP accepted {stack.smtp.count()} for {len(accepted)} jobs')
    p3_oracle.check(stack, accepted={t: 'target_completed' for t in accepted}, source=('go', 2), target=('django', 3),
                    effects={'django': 0, 'go': 0}, completions={'django': len(accepted)})


# -- D03 ----------------------------------------------------------------------------------------------
def d03_pop_before_bookkeeping(stack):
    """The Go transport pops (BRPOP) before recording the unacked entry; a crash in that gap loses nothing."""
    stack.job_handoff()
    to_go(stack)
    task = new_id()
    stack.barrier.hold('reservation:popped', task)
    consumer = stack.go_consumer()
    go_consumer_of(stack)
    go_mail(stack, task)
    arrival = stack.barrier.wait_arrived('reservation:popped', task, 'Go consumer (BRPOP returned)')
    check(not stack.redis_copies(task), f'the gap: no broker copy exists while the consumer holds the pop: '
                                         f'{sorted(stack.redis_copies(task))}')
    stack.kill_tree(consumer, arrival)
    record, _ = handoff(stack, 'go', 2, 'django')
    location = stack.sql('SELECT source_location FROM public.assozeta_handoff_transfers WHERE task_id=%s', (task,))
    check(location == [('ledger',)], f'the job is recovered from the ledger: {location}')
    worker = stack.django_worker()
    wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the recovered job runs once on Django')
    stack.stop(worker)
    check(stack.smtp.count() == 1, f'SMTP accepted {stack.smtp.count()}; expected 1')
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('go', 2), target=('django', 3),
                    effects={'django': 0, 'go': 0}, completions={'django': 1})


def d03_pre_ledger_envelope_in_pop_gap_leaves_no_trace(stack):
    """Documented limit, pinned: an envelope that never reached the ledger (an older release's, published before
    migration 6) and is popped by a consumer that dies before its unacked bookkeeping leaves no trace anywhere; the
    switch then completes without it. Ledger-admitted work survives the same gap (d03_pop_before_bookkeeping)."""
    stack.job_handoff()
    to_go(stack)
    task = new_id()
    stack.barrier.hold('reservation:popped', task)
    consumer = stack.go_consumer()
    go_consumer_of(stack)
    stack.push(GO_QUEUE, stack.envelope(MAIL_TASK, task, kwargs=mail_kwargs('socio@example.test'), queue=GO_QUEUE,
                                        stamp=time.time()))
    arrival = stack.barrier.wait_arrived('reservation:popped', task, 'Go consumer (BRPOP returned)')
    stack.kill_tree(consumer, arrival)
    stack.barrier.release(arrival['point'], arrival['key'])
    check(not stack.redis_copies(task) and stack.work_row(task) is None,
          f'the limit changed (good): a pre-ledger envelope lost in the pop gap is now recorded somewhere '
          f'({sorted(stack.redis_copies(task))}, {stack.work_row(task)}); update this test and the Phase 3 limits')
    handoff(stack, 'go', 2, 'django')
    check(stack.work_row(task) is None and stack.smtp.count() == 0 and stack.task_result(task) is None,
          'the switch completes without the lost pre-ledger envelope, and nothing ran it')


def run_cli(stack, step, record, *, hold=None, expect=0, at_hold=None):
    """Run one controller step as a process; with hold=(point, key) kill it at that barrier, then return.
    at_hold(), if given, runs while the controller is parked at the barrier, before the kill."""
    directory = stack.work / f'cli-barriers-{uuid.uuid4().hex[:8]}'
    directory.mkdir()
    if hold:
        (directory / f'hold.{hold[0]}.{hold[1]}').write_text('')
    process = stack.handoff_cli(step, record, barrier_dir=directory)
    if hold:
        path = directory / f'arrived.{hold[0]}.{hold[1]}'
        try:
            wait_for(path.exists, f'job_handoff.py {step} never passed barrier {hold[0]} for {hold[1]} '
                                  '(contract: --barrier-dir)', timeout=60)
            if at_hold:
                at_hold()
        finally:
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=20)
        return None
    code, output, err = stack.finish_cli(process)
    check(code == expect, f'job_handoff.py {step} exited {code} (expected {expect}): {err}')
    return output


def d03_controller_crashes(stack):
    """The controller dies after a receipt, after the source ack and after the destination publish; reruns converge."""
    stack.job_handoff()
    jobs = [new_id() for _ in range(3)]
    for task in jobs:
        check(publish_mail(stack, task) == ('returned', None), 'Django publication')
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    run_cli(stack, 'close-admission', record)
    run_cli(stack, 'settle', record)
    def receipt_before_removal():
        receipts = stack.sql('SELECT count(*) FROM public.assozeta_handoff_transfers WHERE operation_id=%s AND task_id=%s',
                             (str(operation), jobs[0]))[0][0]
        check(receipts == 1, f'the receipt of {jobs[0]} is durable (committed) at receipt:persisted: {receipts}')
        check(stack.redis_copies(jobs[0]), f'the source copy of {jobs[0]} was removed before its receipt was durable')
    run_cli(stack, 'stage-backlog', record, hold=('receipt:persisted', jobs[0]), at_hold=receipt_before_removal)
    run_cli(stack, 'stage-backlog', record, hold=('source:acked', jobs[1]))
    run_cli(stack, 'stage-backlog', record)
    rows = stack.sql('SELECT task_id, count(*) FROM public.assozeta_handoff_transfers WHERE operation_id=%s GROUP BY task_id',
                     (str(operation),))
    check(sorted(rows) == sorted((task, 1) for task in jobs), f'one receipt per job after repeated recovery: {rows}')
    check(not any(stack.redis_copies(task) for task in jobs), 'no source copy survives the repeated staging')
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    run_cli(stack, 'activate', record, hold=('destination:published', jobs[2]))
    run_cli(stack, 'activate', record)
    consumer = stack.go_consumer()
    wait_for(lambda: all(stack.task_result(t) == 'SUCCESS' for t in jobs), 'every job runs on Go', timeout=60)
    heartbeats_after(stack, go_consumer_of(stack))
    stack.stop(consumer)
    check(stack.smtp.count() == 3, f'SMTP accepted {stack.smtp.count()}; a duplicated envelope must not send twice')
    p3_oracle.check(stack, accepted={t: 'target_completed' for t in jobs}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 0}, completions={'go': 3})


# -- D04 ----------------------------------------------------------------------------------------------
def d04_prefetch_running_held_and_next_month(stack):
    stack.job_handoff()
    stack.seed_balance()
    worker = stack.django_worker()
    stack.django_ready()
    running, soon, month, prefetched = (new_id() for _ in range(4))
    now = datetime.now(timezone.utc)
    soon_eta, month_eta, month_expires = now + timedelta(hours=1), now + timedelta(days=30), now + timedelta(days=31)
    stack.barrier.hold('claim:granted', running)
    check(stack.django_publish(running) == ('returned', None), 'publication')
    stack.barrier.wait_arrived('claim:granted', running, 'Django worker')
    check(stack.django_publish(soon, eta=soon_eta) == ('returned', None), 'publication')
    check(stack.django_publish(month, eta=month_eta, expires=month_expires) == ('returned', None), 'publication')
    check(stack.django_publish(prefetched) == ('returned', None), 'publication')
    wait_for(lambda: stack.redis.hlen('unacked') >= 3, 'the worker prefetched the ETA and the ready jobs')
    started = time.monotonic()
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    stack.barrier.release('claim:granted', running)
    wait_for(lambda: stack.task_result(running) == 'SUCCESS', 'the running job finishes on the source')
    stack.barrier.wait_arrived('claim:refused', prefetched, 'Django worker (prefetched job after the freeze)')
    steps['settle'](record)
    stack.stop(worker)
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    steps['activate'](record)
    check(time.monotonic() - started < 60, 'a job due next month must not delay the switch')
    for task, eta in ((soon, soon_eta), (month, month_eta)):
        job = stack.go_job(task)
        check(job and job[0] == 'pending' and abs((job[7] - eta).total_seconds()) < 1,
              f'{task} keeps its absolute due time on Go: {job and job[7]} != {eta}')
    check(abs((stack.work_row(month)['expires'] - month_expires).total_seconds()) < 1, 'expiry is preserved')
    go = stack.go_worker()
    wait_for(lambda: (stack.go_job(prefetched) or ('',))[0] == 'completed', 'the released prefetched job runs on Go')
    heartbeats_after(stack, consumer_of(stack, 'go')[0]['consumer_id'])
    stack.stop(go)
    p3_oracle.check(stack, accepted={running: 'source_completed', prefetched: 'target_completed', soon: 'target_pending',
                                     month: 'target_pending'},
                    source=('django', 1), target=('go', 2), effects={'django': 1, 'go': 1})


def d04_visibility_restore_races_staging(stack):
    """kombu's visibility restore moves an unacked copy back to the queue while the controller stages it."""
    stack.job_handoff()
    task = new_id()
    check(stack.django_publish(task) == ('returned', None), 'publication')
    raw = stack.redis.rpop('celery')
    tag = json.loads(raw)['properties']['delivery_tag']
    from kombu.utils.json import dumps
    stack.redis.hset('unacked', tag, dumps([json.loads(raw), '', 'celery']))
    stack.redis.zadd('unacked_index', {tag: time.time() - 7200})
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    run_cli(stack, 'close-admission', record)
    run_cli(stack, 'settle', record)
    directory = stack.work / 'race-barriers'
    directory.mkdir()
    (directory / f'hold.receipt:persisted.{task}').write_text('')
    process = stack.handoff_cli('stage-backlog', record, barrier_dir=directory)
    wait_for((directory / f'arrived.receipt:persisted.{task}').exists,
             'job_handoff.py stage-backlog never passed receipt:persisted (contract: --barrier-dir)', timeout=60)
    stack.redis.hdel('unacked', tag)
    stack.redis.zrem('unacked_index', tag)
    stack.redis.rpush('celery', raw)
    (directory / f'release.receipt:persisted.{task}').write_text('')
    code, _, err = stack.finish_cli(process)
    check(code == 0, f'stage-backlog after the race: {err}')
    check(not stack.redis_copies(task), f'a restored copy survived staging: {sorted(stack.redis_copies(task))}')
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    run_cli(stack, 'activate', record)
    go = stack.go_worker()
    wait_for(lambda: (stack.go_job(task) or ('',))[0] == 'completed', 'the raced job runs once on Go')
    stack.stop(go)
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 1})


# -- D06 ----------------------------------------------------------------------------------------------
def d06_retry_on_the_boundary(stack):
    """A send fails and retries (same id, retries+1, countdown) while admission is frozen."""
    stack.job_handoff()
    worker = stack.django_worker()
    stack.django_ready()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(publish_mail(stack, task) == ('returned', None), 'publication')
    stack.barrier.wait_arrived('claim:granted', task, 'Django worker')
    original = json.loads(bytes(stack.work_row(task)['envelope']))
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    stack.smtp.failing = True
    stack.barrier.release('claim:granted', task)
    wait_for(lambda: (stack.work_row(task) or {}).get('retries') == 1, 'the failed send publishes its retry')
    row = stack.work_row(task)
    check(row['state'] == 'held' and not stack.redis_copies(task),
          f'a retry published during the freeze is held durably, not queued: {row["state"]}, {stack.redis_copies(task)}')
    steps['settle'](record)
    stack.stop(worker)
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    steps['activate'](record)
    message = json.loads(stack.redis.lrange(GO_QUEUE, 0, -1)[0])
    headers = message['headers']
    # Celery's own retry (signature_from_request(...).apply_async() inside the task) keeps the root and makes the
    # retrying task the parent; the transferred retry must be that message, not a rewritten one.
    check(headers['id'] == task and headers['retries'] == 1 and headers['root_id'] == original['headers']['root_id']
          and headers['parent_id'] == task
          and headers['assozeta_published_at'] == original['headers']['assozeta_published_at'],
          f'the retry keeps id, root id (parent = the retrying task, as Celery sets it) and the restore stamp: {headers}')
    eta = datetime.fromisoformat(headers['eta'])
    check(eta > datetime.now(timezone.utc) + timedelta(seconds=100) and abs((eta - row['eta']).total_seconds()) < 1,
          f'the retry countdown is kept as its absolute eta: {headers["eta"]}')


def d06_child_during_drain(stack):
    """A running parent publishes a child during the freeze; the child keeps its lineage and runs once on the target."""
    stack.job_handoff()
    from communications.models import CommunicationConfiguration
    from application.models import SportAssociation, User
    owner, association = uuid.UUID(int=0xA3000101), uuid.UUID(int=0xA3000111)
    User.original_objects.bulk_create([User(user_id=owner, username='handoff-child-owner', role=1, password='!private-fixture',
                                            email='child-owner@example.test')])
    SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=association, user_id=owner,
                                                                    denomination='ASD Figlio', tax_code='00000000000')])
    CommunicationConfiguration.objects.bulk_create([CommunicationConfiguration(
        communication_configuration_id=uuid.UUID(int=0xA3000131), sport_association_id=association, email_smtp_host=None,
        email_smtp_port=None, email_smtp_user=None, email_smtp_password=None, email_sender_name='Figlio',
        email_encryption='NONE', daily_email_limit=1000, daily_email_balance=0)])
    worker = stack.django_worker()
    stack.django_ready()
    parent = new_id()
    stack.barrier.hold('claim:granted', parent)
    check(publish_mail(stack, parent, kwargs=mail_kwargs('socio@example.test', sport_association_id=str(association),
                                                         html_message='<p>via associazione</p>')) == ('returned', None),
          'publication')
    stack.barrier.wait_arrived('claim:granted', parent, 'Django worker')
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    stack.barrier.release('claim:granted', parent)
    wait_for(lambda: stack.task_result(parent) == 'SUCCESS', 'the parent finishes on the source')
    children = [row[0] for row in stack.sql('SELECT task_id FROM public.assozeta_handoff_work WHERE parent_id=%s', (parent,))]
    check(len(children) == 1, f'the parent published exactly one child into the ledger: {children}')
    child = stack.work_row(children[0])
    check(child['state'] == 'held' and child['root_id'] == parent and not stack.redis_copies(child['task_id']),
          f'the child is held with its lineage: {child["state"]}, root {child["root_id"]}')
    steps['settle'](record)
    stack.stop(worker)
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    steps['activate'](record)
    message = json.loads(stack.redis.lrange(GO_QUEUE, 0, -1)[0])
    check(message['headers']['id'] == child['task_id'] and message['headers']['parent_id'] == parent
          and message['headers']['root_id'] == parent, f'the delivered child keeps parent/root ids: {message["headers"]}')
    consumer = stack.go_consumer()
    wait_for(lambda: stack.task_result(child['task_id']) == 'SUCCESS', 'the child runs once on Go')
    stack.stop(consumer)
    check(stack.smtp.count() == 1, f'SMTP accepted {stack.smtp.count()}; the child sends once')
    p3_oracle.check(stack, accepted={parent: 'source_completed'}, children={child['task_id']: 'target_completed'},
                    source=('django', 1), target=('go', 2), effects={'django': 0, 'go': 0},
                    completions={'django': 1, 'go': 1})


def d06_expiry_applies_on_target(stack):
    stack.job_handoff()
    task = new_id()
    expires = datetime.now(timezone.utc) + timedelta(seconds=4)
    check(publish_mail(stack, task, expires=expires) == ('returned', None), 'publication')
    handoff(stack, 'django', 1, 'go')
    check(abs((stack.work_row(task)['expires'] - expires).total_seconds()) < 1, 'the original expiry is transferred')
    wait_for(lambda: datetime.now(timezone.utc) > expires + timedelta(seconds=1), 'expiry passes', timeout=20)
    consumer = stack.go_consumer()
    wait_for(lambda: stack.task_result(task) == 'REVOKED', 'the expired job is revoked on the target (Celery semantics)')
    stack.stop(consumer)
    check(stack.smtp.count() == 0 and stack.work_row(task)['state'] == 'expired',
          f'an expired job never runs; ledger state {stack.work_row(task)["state"]}')


# -- D10 ----------------------------------------------------------------------------------------------
def d10_legacy_and_unsupported_messages(stack):
    """Envelopes of an older release (Phase 0 legacy-mixed fixture) and unsupported ones, Django -> Go."""
    module, store, broker = stack.handoff_parts()
    check(hasattr(module, 'TransferBlocked') and hasattr(module, 'reconcile'), 'contract: TransferBlocked/reconcile missing')
    from kombu.utils.json import dumps
    from TESTS.handoff import fixtures
    legacy = {}
    for queue, envelopes in fixtures.load('legacy-mixed').broker.items():
        for envelope in envelopes:
            if envelope['headers']['task'] == 'communications.tasks.deliver_whatsapp':
                continue  # Phase 4 gives the legacy WhatsApp poll a Go handler; its disposition is tested there
            payload = dumps(envelope).encode()
            stack.push(queue, payload, envelope['properties'].get('priority') or 0)
            legacy[envelope['headers']['id']] = (envelope['headers']['task'], queue, payload)
    unknown, pickled, chained, unstamped, django_only = new_id(), new_id(), new_id(), new_id(), new_id()
    stack.push('celery', stack.envelope('retired.task.name', unknown, stamp=time.time()))
    stack.push('celery', stack.envelope(DB_TASK, pickled, stamp=time.time(), content_type='application/x-python-serialize'))
    stack.push('celery', stack.envelope(DB_TASK, chained, stamp=time.time(), chain=[{'task': DB_TASK}]))
    stack.push('celery', stack.envelope(DB_TASK, unstamped))
    django_only_payload = stack.envelope('fixture.early_ack', django_only, stamp=time.time())  # no Go handler
    stack.push('celery', django_only_payload)
    expected = [(unknown, 'unknown_task'), (pickled, 'unsupported_encoding'), (chained, 'unsupported_option'),
                (django_only, 'no_target_handler')]
    snapshot = stack.broker_snapshot()
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    check('preflight' in steps, 'contract: handoff_steps returns a preflight step')
    report = steps['preflight'](record)
    found = sorted((item['task_id'], item['reason']) for item in report['blockers'])
    check(found == sorted(expected), f'preflight identifies the blocking work: {found}')
    check(stack.broker_snapshot() == snapshot and not stack.sql('SELECT count(*) FROM public.assozeta_handoff_work')[0][0],
          'preflight is read-only')
    steps['close_admission'](record)
    steps['settle'](record)
    for attempt in range(2):
        blocked(stack, steps['stage_backlog'], record, expected)
    raw = {unknown: None, pickled: None, chained: None, django_only: django_only_payload}
    for task, reason in expected:
        row = stack.work_row(task)
        check(row and (row['state'], row['block_reason']) == ('blocked', reason), f'{task} is in protected staging: {row}')
        if raw[task] is not None:
            check(bytes(row['envelope']) == raw[task], f'{task}: the original envelope is preserved byte for byte')
        check(not stack.redis_copies(task), f'{task} must leave the source queue (no poison loop): {stack.redis_copies(task)}')
    adopted = list(legacy) + [unstamped]
    for task in adopted:
        check(stack.work_row(task)['state'] == 'staged', f'legacy envelope {task} is adopted and staged once')
    check(stack.work_row(unstamped)['accepted_at'] == datetime.fromtimestamp(0, timezone.utc),
          'an envelope without assozeta_published_at keeps "no stamp" semantics (accepted_at = epoch)')
    for task, _ in expected:
        reconcile(stack, task, 'discard')
        check(stack.work_row(task)['state'] == 'cancelled', f'{task}: discard is a recorded terminal decision')
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    steps['activate'](record)
    mail = next(t for t, (name, _, _) in legacy.items() if name == MAIL_TASK)
    check(stack.queued_ids(stack.priority_queue(GO_QUEUE, 3)) == [mail], 'the legacy email keeps its priority on the Go queue')
    for task in adopted:
        if task != mail:
            check(stack.go_job(task) is not None, f'legacy durable-kind envelope {task} becomes a Go durable row')
    verify = steps['verify'](record)
    check(not verify.get('blocked') and not verify.get('undelivered'), f'verify after resolution: {verify}')


# -- D15 ----------------------------------------------------------------------------------------------
def d15_tenants_and_batches(stack):
    stack.job_handoff()
    from application.models import SportAssociation, User
    tenants = [uuid.UUID(int=0xA3000201 + i) for i in range(2)]
    User.original_objects.bulk_create([User(user_id=uuid.UUID(int=0xA3000301 + i), username=f'tenant-owner-{i}', role=1,
                                            password='!private-fixture', email=f'tenant{i}@example.test') for i in range(2)])
    SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=t, user_id=uuid.UUID(int=0xA3000301 + i),
                                                                    denomination=f'ASD Tenant {i}', tax_code='00000000000')
                                                   for i, t in enumerate(tenants)])
    jobs = {}
    for index in range(23):
        tenant = tenants[index % 2]
        task = new_id()
        check(publish_mail(stack, task, kwargs=mail_kwargs('stesso@example.test', sport_association_id=str(tenant)))
              == ('returned', None), 'publication')
        jobs[task] = tenant
    timings = {}
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0, batch_size=5)
    for step in ('close_admission', 'settle', 'stage_backlog'):
        started = time.monotonic()
        steps[step](record)
        timings[step] = round(time.monotonic() - started, 2)
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    started = time.monotonic()
    steps['activate'](record)
    timings['activate'] = round(time.monotonic() - started, 2)
    check(sorted(stack.queued_ids(GO_QUEUE)) == sorted(jobs), 'every job of both tenants is delivered once (no cross-tenant dedup)')
    consumer = stack.go_consumer()
    wait_for(lambda: all(stack.task_result(t) == 'SUCCESS' for t in jobs), 'both tenants complete on Go', timeout=120)
    stack.stop(consumer)
    per_tenant = dict(stack.sql('SELECT sport_association_id, count(*) FROM application_emaillog GROUP BY sport_association_id'))
    check(per_tenant == {tenants[0]: 12, tenants[1]: 11}, f'effects per tenant: {per_tenant}')
    check(stack.smtp.count() == 23, f'SMTP accepted {stack.smtp.count()} for 23 jobs')
    print('d15 timings', json.dumps(timings), flush=True)
    p3_oracle.check(stack, accepted={t: 'target_completed' for t in jobs}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 0}, completions={'go': 23})


SCENARIOS = {
    'd02_priorities_forward': d02_priorities_forward,
    'd02_priorities_reverse': d02_priorities_reverse,
    'd03_pop_before_bookkeeping': d03_pop_before_bookkeeping,
    'd03_controller_crashes': d03_controller_crashes,
    'd03_pre_ledger_envelope_in_pop_gap_leaves_no_trace': d03_pre_ledger_envelope_in_pop_gap_leaves_no_trace,
    'd04_prefetch_running_held_and_next_month': d04_prefetch_running_held_and_next_month,
    'd04_visibility_restore_races_staging': d04_visibility_restore_races_staging,
    'd06_retry_on_the_boundary': d06_retry_on_the_boundary,
    'd06_child_during_drain': d06_child_during_drain,
    'd06_expiry_applies_on_target': d06_expiry_applies_on_target,
    'd10_legacy_and_unsupported_messages': d10_legacy_and_unsupported_messages,
    'd15_tenants_and_batches': d15_tenants_and_batches,
}
assert ContractFailure
