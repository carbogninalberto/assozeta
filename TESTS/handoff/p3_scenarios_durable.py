"""Phase 3 part B: running attempts and SQL jobs (D05, D07, D13, abort). Contract: test_p3_email_job.py docstring."""
from datetime import datetime, timezone
import json
import signal
import threading
import time
import uuid

from TESTS.handoff import p3_oracle
from TESTS.handoff.p3_scenarios_db import consumer_of, expect_rejected_go, heartbeats_after, new_id, to_go
from TESTS.handoff.p3_scenarios_email import blocked, handoff
from TESTS.handoff.p3_stack import DB_TASK, NIL, ContractFailure, check, wait_for

EARLY_ACK = 'fixture.early_ack'


def send_early_ack(stack, task_id):
    """Django producer path: app.send_task (the test-only early-acknowledgement task)."""
    stack.app.send_task(EARLY_ACK, task_id=task_id, kwargs={'marker': task_id})


def stop_process(process, arrival=None):
    for pid in ({arrival['pid']} if arrival else set()) | {process.pid}:
        try:
            import os
            os.kill(int(pid), signal.SIGSTOP)
        except ProcessLookupError:
            pass


def continue_process(process, arrival=None):
    import os
    for pid in ({arrival['pid']} if arrival else set()) | {process.pid}:
        try:
            os.kill(int(pid), signal.SIGCONT)
        except ProcessLookupError:
            pass


# -- D05 ----------------------------------------------------------------------------------------------
def d05_early_ack_zero_depth_holds_settle(stack):
    """The broker is empty while an early-acked job runs: settle must not pass on queue depth."""
    stack.job_handoff()
    stack.seed_balance()
    worker = stack.django_worker()
    stack.django_ready()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    send_early_ack(stack, task)
    stack.barrier.wait_arrived('claim:granted', task, 'Django worker (fixture.early_ack)')
    check(not stack.redis_copies(task), f'early acknowledgement: no broker copy while it runs ({sorted(stack.redis_copies(task))})')
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    quick = stack.handoff_steps(heartbeat_grace=1.0, settle_timeout=2.0)
    blocked(stack, quick['settle'], record, [(task, 'running')])
    stack.barrier.release('claim:granted', task)
    wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the early-acked job finishes on the source')
    steps['settle'](record)
    stack.stop(worker)
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'django', 1, 'go')) == ('committed', 'go', 2), 'commit')
    steps['activate'](record)
    p3_oracle.check(stack, accepted={task: 'source_completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 1, 'go': 0})


def d05_killed_attempt_is_released_and_runs_once(stack):
    stack.job_handoff()
    stack.seed_balance()
    worker = stack.django_worker()
    stack.django_ready()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(stack.django_publish(task) == ('returned', None), 'publication')
    arrival = stack.barrier.wait_arrived('claim:granted', task, 'Django worker')
    stack.kill_tree(worker, arrival)
    handoff(stack, 'django', 1, 'go')
    go = stack.go_worker()
    wait_for(lambda: (stack.go_job(task) or ('',))[0] == 'completed', 'the released job runs once on Go')
    stack.stop(go)
    outcomes = sorted((a['runtime'], a['outcome']) for a in stack.attempts(task_id=task))
    check(outcomes == [('django', 'released'), ('go', 'completed')], f'attempts: {outcomes}')
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 1})


def d05_stopped_worker_cannot_complete_later(stack):
    """A Django worker frozen by SIGSTOP holds a claim; after the switch it resumes: its stale completion is fenced."""
    stack.job_handoff()
    stack.seed_balance()
    worker = stack.django_worker()
    stack.django_ready()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(stack.django_publish(task) == ('returned', None), 'publication')
    arrival = stack.barrier.wait_arrived('claim:granted', task, 'Django worker')
    stop_process(worker, arrival)
    try:
        handoff(stack, 'django', 1, 'go')
        go = stack.go_worker()
        wait_for(lambda: (stack.go_job(task) or ('',))[0] == 'completed', 'the job runs once on Go')
        stack.stop(go)
    finally:
        continue_process(worker, arrival)
    stack.barrier.release('claim:granted', task)
    stack.barrier.wait_arrived('attempt:fenced', task, 'the resumed Django worker (stale completion)')
    check(stack.effects() == {'assozeta-go-worker': 1}, f'only the Go execution committed an effect: {stack.effects()}')
    stack.stop(worker)
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 1})


def d05_go_lease_holder_dies(stack):
    """Go -> Django: the durable worker dies holding a lease; settle waits for the lease, the job runs once on Django."""
    stack.job_handoff()
    to_go(stack)
    stack.seed_balance()
    worker = stack.go_worker()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(stack.go_enqueue(task).get('outcome') == 'enqueued', 'Go enqueue')
    arrival = stack.barrier.wait_arrived('claim:granted', task, 'Go durable worker')
    stack.kill_tree(worker, arrival)
    lease_until = stack.sql('SELECT lease_until FROM public.assozeta_go_jobs WHERE id=%s', (task,))[0][0]
    operation = uuid.uuid4()
    record = stack.record(operation, 'go', 2, 'django')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    steps['settle'](record)
    check(datetime.now(timezone.utc) >= lease_until, 'settle released a Go attempt before its lease expired')
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'go', 2, 'django')) == ('committed', 'django', 3), 'commit')
    steps['activate'](record)
    django = stack.django_worker()
    wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the job runs once on Django')
    stack.stop(django)
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('go', 2), target=('django', 3),
                    effects={'django': 1, 'go': 0})


def d05_stopped_go_worker_resumes_before_commit(stack):
    """A SIGSTOPped Go worker resumes after settle released its expired lease: its effect is fenced (no double run)."""
    stack.job_handoff()
    to_go(stack)
    stack.seed_balance()
    worker = stack.go_worker()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(stack.go_enqueue(task).get('outcome') == 'enqueued', 'Go enqueue')
    arrival = stack.barrier.wait_arrived('claim:granted', task, 'Go durable worker')
    stop_process(worker, arrival)
    operation = uuid.uuid4()
    record = stack.record(operation, 'go', 2, 'django')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    try:
        steps['close_admission'](record)
        steps['settle'](record)
    finally:
        continue_process(worker, arrival)
    stack.barrier.release('claim:granted', task)
    stack.barrier.wait_arrived('attempt:fenced', task, 'the resumed Go worker (expired lease)')
    check(stack.effect_total() == 0, 'a Go attempt whose lease settle released committed its effect')
    stack.stop(worker)
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'go', 2, 'django')) == ('committed', 'django', 3), 'commit')
    steps['activate'](record)
    django = stack.django_worker()
    wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the job runs once on Django')
    stack.stop(django)
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('go', 2), target=('django', 3),
                    effects={'django': 1, 'go': 0})


# -- D07 ----------------------------------------------------------------------------------------------
def d07_go_rows_on_reverse(stack):
    """Pending, expired-lease, completed, failed and historical (pre-ledger) Go rows on Go -> Django."""
    stack.job_handoff()
    to_go(stack)
    stack.seed_balance()
    pending, leased, done, failed = (new_id() for _ in range(4))
    worker = stack.go_worker()
    check(stack.go_enqueue(done).get('outcome') == 'enqueued', 'Go enqueue')
    wait_for(lambda: (stack.go_job(done) or ('',))[0] == 'completed', 'the completed row')
    check(stack.go_enqueue(failed, payload='{"unexpected": true}').get('outcome') == 'enqueued', 'Go enqueue')
    wait_for(lambda: (stack.go_job(failed) or ('',))[0] == 'failed', 'the failed row (invalid payload is terminal)')
    stack.barrier.hold('claim:granted', leased)
    check(stack.go_enqueue(leased).get('outcome') == 'enqueued', 'Go enqueue')
    arrival = stack.barrier.wait_arrived('claim:granted', leased, 'Go durable worker')
    stack.kill_tree(worker, arrival)
    check(stack.go_enqueue(pending, available_at=datetime.now(timezone.utc)).get('outcome') == 'enqueued', 'Go enqueue')
    historical = new_id()
    stack.sql('''INSERT INTO public.assozeta_go_jobs (id, association_id, kind, version, idempotency_key, payload, max_attempts,
      available_at) VALUES (%s, %s, %s, 1, %s, '{}', 3, clock_timestamp())''', (historical, NIL, DB_TASK, 'historical:' + historical))
    terminal = {task: stack.go_job(task) for task in (done, failed)}
    record, _ = handoff(stack, 'go', 2, 'django')
    queued = {json.loads(raw)['headers']['id']: json.loads(raw) for raw in stack.redis.lrange('celery', 0, -1)}
    check(set(queued) == {pending, leased, historical}, f'exactly the pending, expired-lease and historical rows cross: {sorted(queued)}')
    check(queued[leased]['headers']['retries'] == 1, 'the expired-lease row keeps its spent attempt as retries=1')
    for task, job in terminal.items():
        check(stack.go_job(task) == job and not stack.redis_copies(task), f'terminal Go row {task} stays terminal and untouched')
    django = stack.django_worker()
    wait_for(lambda: all(stack.task_result(t) == 'SUCCESS' for t in (pending, leased, historical)), 'all three run on Django')
    stack.stop(django)
    p3_oracle.check(stack, accepted={done: 'source_completed', failed: 'terminal:failed', pending: 'target_completed',
                                     leased: 'target_completed', historical: 'target_completed'},
                    source=('go', 2), target=('django', 3), effects={'go': 1, 'django': 3})


def d07_legacy_broker_task_to_go_durable(stack):
    """Django -> Go: legacy envelopes (no ledger row; one without the restore stamp) become Go durable rows."""
    stack.job_handoff()
    stack.seed_balance()
    stamped, unstamped = new_id(), new_id()
    stamp = time.time() - 60
    stack.push('celery', stack.envelope(DB_TASK, stamped, stamp=stamp))
    stack.push('celery', stack.envelope(DB_TASK, unstamped))
    handoff(stack, 'django', 1, 'go')
    job = stack.go_job(stamped)
    check(job and abs(job[6].timestamp() - stamp) < 1e-3, f'the adopted row keeps the publication stamp as created_at: {job}')
    check(stack.go_job(unstamped)[6] == datetime.fromtimestamp(0, timezone.utc), 'no stamp maps to the epoch')
    go = stack.go_worker()
    wait_for(lambda: all((stack.go_job(t) or ('',))[0] == 'completed' for t in (stamped, unstamped)), 'both run on Go')
    stack.stop(go)
    p3_oracle.check(stack, accepted={stamped: 'target_completed', unstamped: 'target_completed'},
                    source=('django', 1), target=('go', 2), effects={'django': 0, 'go': 2})


# -- D13 and abort --------------------------------------------------------------------------------------
def d13_rollback_with_target_backlog(stack):
    stack.job_handoff()
    stack.seed_balance()
    staged = new_id()
    check(stack.django_publish(staged) == ('returned', None), 'publication')
    first, _ = handoff(stack, 'django', 1, 'go')
    arrivals = [new_id(), new_id()]
    for task in arrivals:
        check(stack.go_enqueue(task).get('outcome') == 'enqueued', 'the target accepts new work after commit')
    second, _ = handoff(stack, 'go', 2, 'django')
    expect_rejected_go(stack, new_id())
    django = stack.django_worker()
    wait_for(lambda: all(stack.task_result(t) == 'SUCCESS' for t in [staged, *arrivals]), 'rollback backlog runs on Django')
    stack.stop(django)
    audit = sorted(stack.sql('SELECT operation_id::text, task_id, target_runtime, target_generation FROM '
                             'public.assozeta_handoff_transfers'))
    expected = sorted([(first['id'], staged, 'go', 2)] + [(second['id'], t, 'django', 3) for t in [staged, *arrivals]])
    check(audit == expected, f'transfer audit shows both generations: {audit}')
    p3_oracle.check(stack, accepted={t: 'target_completed' for t in [staged, *arrivals]}, source=('go', 2),
                    target=('django', 3), effects={'django': 3, 'go': 0})


def abort_before_commit_returns_work_to_source(stack):
    module, store, broker = stack.handoff_parts()
    check(hasattr(module, 'abort'), 'contract: job_handoff.abort(store, broker, record) is missing')
    stack.seed_balance()
    queued = new_id()
    check(stack.django_publish(queued) == ('returned', None), 'publication')
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    held = new_id()
    check(stack.django_publish(held) == ('returned', None), 'publication during the freeze')
    steps['settle'](record)
    steps['stage_backlog'](record)
    stack.contract_call('abort', module.abort, module)(store, broker, record)
    check(stack.sql('SELECT runtime, generation, admission FROM public.assozeta_go_job_ownership')[0] == ('django', 1, 'open'),
          'abort keeps the source generation and reopens admission')
    for task in (queued, held):
        check(stack.work_row(task)['state'] == 'pending' and 'celery' in stack.redis_copies(task),
              f'{task} is returned to the source queue: {stack.work_row(task)["state"]}')
    worker = stack.django_worker()
    wait_for(lambda: all(stack.task_result(t) == 'SUCCESS' for t in (queued, held)), 'both run once on the source')
    stack.stop(worker)
    p3_oracle.check(stack, accepted={queued: 'source_completed', held: 'source_completed'}, source=('django', 1),
                    target=('go', 2), effects={'django': 2, 'go': 0})


SCENARIOS = {
    'd05_early_ack_zero_depth_holds_settle': d05_early_ack_zero_depth_holds_settle,
    'd05_killed_attempt_is_released_and_runs_once': d05_killed_attempt_is_released_and_runs_once,
    'd05_stopped_worker_cannot_complete_later': d05_stopped_worker_cannot_complete_later,
    'd05_go_lease_holder_dies': d05_go_lease_holder_dies,
    'd05_stopped_go_worker_resumes_before_commit': d05_stopped_go_worker_resumes_before_commit,
    'd07_go_rows_on_reverse': d07_go_rows_on_reverse,
    'd07_legacy_broker_task_to_go_durable': d07_legacy_broker_task_to_go_durable,
    'd13_rollback_with_target_backlog': d13_rollback_with_target_backlog,
    'abort_before_commit_returns_work_to_source': abort_before_commit_returns_work_to_source,
}
assert threading and ContractFailure
