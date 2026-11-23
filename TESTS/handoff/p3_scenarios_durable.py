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
from TESTS.handoff.p3_stack import DB_TASK, GO_WORKER_APP, NIL, ContractFailure, check, wait_for

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


def stop_quiescent(stack, process, application, arrival=None):
    """SIGSTOP a process at a moment when none of its sessions is inside a transaction.

    A process frozen inside an open transaction (another executor slot polling, say) holds its row locks until it
    resumes, and the controller's close_admission then waits on them; that is a different situation (the switch
    fails safe on its statement timeout) from the one these scenarios pin: a frozen worker holding only a lease."""
    for _ in range(200):
        stop_process(process, arrival)
        busy = stack.sql("SELECT count(*) FROM pg_stat_activity WHERE application_name=%s AND xact_start IS NOT NULL",
                         (application,))[0][0]
        if not busy:
            return
        continue_process(process, arrival)
        time.sleep(0.02)
    raise ContractFailure(f'{application} never reached a moment without an open transaction')


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
    # The target attempt carries the same task id, so it passes the same barrier key: lift the source's hold
    # once the source can no longer move (killed or stopped), or the target waits on it too.
    stack.barrier.release(arrival['point'], arrival['key'])
    handoff(stack, 'django', 1, 'go')
    go = stack.go_worker()
    wait_for(lambda: (stack.go_job(task) or ('',))[0] == 'completed', 'the released job runs once on Go')
    stack.stop(go)
    outcomes = sorted((a['runtime'], a['outcome']) for a in stack.attempts(task_id=task))
    check(outcomes == [('django', 'released'), ('go', 'completed')], f'attempts: {outcomes}')
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 1})


def late_ack_redelivery_after_worker_loss_runs_again(stack, worker_env=None, backlog=False):
    """No switch: an installation that never switches keeps Celery's at-least-once redelivery for acks_late tasks.
    instance.restore.execute (acks_late, reject_on_worker_lost) loses its prefork child after the claim; Celery
    requeues the message and the redelivered copy must run (the lost attempt is released, not left 'running').
    With backlog=True the message is already queued when the worker starts (received while it boots)."""
    from instance.tasks import restore_instance_data
    stack.job_handoff()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    if backlog:
        restore_instance_data.apply_async(args=[str(uuid.uuid4())], task_id=task)
    worker = stack.django_worker(env=worker_env)
    if not backlog:
        stack.django_ready()
        restore_instance_data.apply_async(args=[str(uuid.uuid4())], task_id=task)
    arrival = stack.barrier.wait_arrived('claim:granted', task, 'Django worker (prefork child)')
    check(arrival.get('pid') and arrival['pid'] != worker.pid, f'the claim runs in a prefork child: {arrival}')
    stack.kill(arrival['pid'])  # the child only: the pool sees WorkerLostError and requeues the late-ack message
    stack.barrier.release(arrival['point'], arrival['key'])
    try:
        samples = []

        def outcome():
            if not samples or time.monotonic() - samples[-1][0] >= 5:
                samples.append((time.monotonic(), stack.sql(
                    "SELECT round(extract(epoch FROM lease_until - clock_timestamp())::numeric, 1) FROM "
                    "public.assozeta_handoff_attempts WHERE task_id=%s ORDER BY claimed_at", (task,))))
            if stack.barrier.arrival('claim:refused', task):
                raise ContractFailure('the redelivered restore command was refused at claim (the lost attempt is still '
                                      f"'running': {[a['outcome'] for a in stack.attempts(task_id=task)]}); "
                                      'Celery would have run it again')
            return len(stack.attempts(task_id=task)) >= 2 and stack.task_result(task)
        try:
            wait_for(outcome, 'the redelivered restore command is claimed again and finishes', timeout=90)
        except ContractFailure as exc:
            sessions = stack.sql("SELECT application_name, state, wait_event_type, left(query, 90) FROM pg_stat_activity "
                                 "WHERE datname=current_database() AND pid<>pg_backend_pid() AND state<>'idle'")
            lease = stack.sql("SELECT lease_until, lease_until <= clock_timestamp() FROM public.assozeta_handoff_attempts "
                              "WHERE task_id=%s", (task,))
            try:
                inspect = stack.app.control.inspect(timeout=3)
                pool = {'active': inspect.active(), 'reserved': inspect.reserved(), 'stats': {
                    k: v.get('pool', {}).get('processes') for k, v in (inspect.stats() or {}).items()}}
            except Exception as error:  # diagnostics only
                pool = repr(error)
            raise ContractFailure(f'{exc} (worker pool {pool}, work row state {stack.work_row(task)["state"]}, attempts '
                                  f'{[a["outcome"] for a in stack.attempts(task_id=task)]}, lease {lease}, '
                                  f'busy sessions {sessions}, queue {stack.redis.llen("celery")}, '
                                  f'lease seconds left every 5 s after the kill {[row for _, row in samples]}, '
                                  f'copy refused {bool(stack.barrier.arrival("claim:refused", task))}, '
                                  f'unacked {stack.redis.hlen("unacked")})') from None
    finally:
        stack.stop(worker)
    outcomes = [a['outcome'] for a in stack.attempts(task_id=task)]
    check(outcomes[0] == 'released' and outcomes[1] in ('completed', 'failed'),
          f'the lost attempt is released and the redelivery runs once: {outcomes}')


def redelivery_while_the_executor_runs_waits(stack):
    """No switch: a visibility restore redelivers a message whose executor (a prefork child) is still running.
    The redelivered copy reaches the other child, which must neither run it nor steal the live attempt; once the
    first execution completes, the copy is a duplicate."""
    import json as _json
    stack.job_handoff()
    stack.seed_balance()
    worker = stack.django_worker(concurrency=2)
    stack.django_ready()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(stack.django_publish(task) == ('returned', None), 'publication')
    first = stack.barrier.wait_arrived('claim:granted', task, 'Django worker (first child)')
    envelope = _json.loads(bytes(stack.work_row(task)['envelope']))
    envelope['properties'].setdefault('delivery_info', {})['redelivered'] = True  # what kombu's restore sets
    envelope['headers']['redelivered'] = True
    stack.redis.lpush('celery', _json.dumps(envelope))
    try:
        wait_for(lambda: stack.redis.llen('celery') == 0, 'the other child reserved the redelivered copy', timeout=30)
        time.sleep(2)  # the copy's claim must keep answering 'active' while the first child lives
        check(len(stack.attempts(task_id=task)) == 1 and stack.effect_total() == 0,
              f'the redelivered copy stole or ran the live attempt: {[a["outcome"] for a in stack.attempts(task_id=task)]}')
        stack.barrier.release('claim:granted', task)
        wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the first execution completes')
        wait_for(lambda: stack.barrier.arrival('claim:refused', task), 'the copy is refused once the first completes',
                 timeout=30)
    finally:
        stack.stop(worker)
    check([a['outcome'] for a in stack.attempts(task_id=task)] == ['completed'] and stack.effect_total() == 1,
          f'one attempt, one effect: {[a["outcome"] for a in stack.attempts(task_id=task)]}, {stack.effect_total()}')
    check(first['pid'] != worker.pid, 'the first execution ran in a prefork child')


def probe_redelivery(stack, runtime, task):
    """A redelivered copy of the attempt's own delivery, claimed straight through SQL on a fresh connection."""
    import psycopg2
    from TESTS.handoff.p3_stack import DATABASE, USER
    tag = stack.sql("SELECT delivery_tag FROM public.assozeta_handoff_attempts WHERE task_id=%s AND outcome='running'",
                    (task,))[0][0]
    connection = psycopg2.connect(host=str(stack.work), user=USER, dbname=DATABASE, application_name='handoff-probe')
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT outcome FROM public.assozeta_handoff_claim(%s,%s,%s,%s,NULL,%s,true)',
                           (runtime, 'probe', task, stack.work_row(task)['task_name'], tag))
            return cursor.fetchone()[0]
    finally:
        connection.close()


def outlive_the_first_lease(stack, task):
    """Wait past the first 30 s lease on the database clock while the executor stays parked."""
    first = stack.sql("SELECT claimed_at + interval '35 seconds' FROM public.assozeta_handoff_attempts "
                      "WHERE task_id=%s AND outcome='running'", (task,))[0][0]
    wait_for(lambda: stack.sql('SELECT clock_timestamp() >= %s', (first,))[0][0], 'the first lease period passes',
             timeout=60)


def long_execution_keeps_its_lease_django(stack, kill_parent=False, child_dies_too=False):
    """No switch: a Django execution parked longer than one lease keeps it (renewed by the pool parent, or by the
    child itself once the parent is gone), so its own redelivery still answers 'active' and nothing is recovered."""
    stack.job_handoff()
    stack.seed_balance()
    worker = stack.django_worker()
    stack.django_ready()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(stack.django_publish(task) == ('returned', None), 'publication')
    arrival = stack.barrier.wait_arrived('claim:granted', task, 'Django worker (prefork child)')
    try:
        if kill_parent:
            check(arrival['pid'] != worker.pid, 'the execution runs in a prefork child')
            worker.kill()
            worker.wait(timeout=20)
            if child_dies_too:  # what PR_SET_PDEATHSIG does on Linux, forced on any platform
                stack.kill(arrival['pid'])
            deadline = time.monotonic() + 5  # give a parent-death signal time to land
            while process_alive(arrival['pid']) and time.monotonic() < deadline:
                time.sleep(0.1)
            if not process_alive(arrival['pid']):
                # Linux: Celery's prefork initializer sets PR_SET_PDEATHSIG=SIGKILL, so the child dies with its
                # pool parent. No executor is left: nobody may keep the lease alive, and once it lapses on the
                # database clock its own redelivery recovers the attempt.
                outlive_the_first_lease(stack, task)
                live = stack.sql("SELECT lease_until > clock_timestamp() FROM public.assozeta_handoff_attempts "
                                 "WHERE task_id=%s AND outcome='running'", (task,))[0][0]
                check(not live, 'a lease whose executor died with its parent is not renewed')
                check(probe_redelivery(stack, 'django', task) == 'claimed',
                      'its redelivery recovers the attempt once the lease has expired')
                outcomes = [a['outcome'] for a in stack.attempts(task_id=task)]
                check(outcomes == ['released', 'running'] and stack.effect_total() == 0,
                      f'the dead execution is released, nothing ran twice: {outcomes}')
                return
        outlive_the_first_lease(stack, task)
        live = stack.sql("SELECT lease_until > clock_timestamp() FROM public.assozeta_handoff_attempts "
                         "WHERE task_id=%s AND outcome='running'", (task,))[0][0]
        check(live, 'the parked execution renewed its lease past the first 30 s')
        check(probe_redelivery(stack, 'django', task) == 'active', 'its redelivery waits while the executor lives')
        stack.barrier.release('claim:granted', task)
        wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the parked execution completes')
    finally:
        stack.barrier.release('claim:granted', task)
        if kill_parent:
            stack.kill(arrival['pid'])
        stack.stop(worker)
    outcomes = [a['outcome'] for a in stack.attempts(task_id=task)]
    check(outcomes == ['completed'] and stack.effect_total() == 1, f'one attempt, one effect: {outcomes}')


def process_alive(pid):
    import os
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie (killed, not yet reaped) is gone for our purpose
        waited, _ = os.waitpid(int(pid), os.WNOHANG)
        return waited == 0
    except ChildProcessError:
        return True


def orphan_child_keeps_its_lease(stack):
    """The pool parent dies while its child executes. On macOS the child survives and must renew its own lease;
    on Linux Celery's PR_SET_PDEATHSIG kills the child with its parent, and the lease must then lapse."""
    long_execution_keeps_its_lease_django(stack, kill_parent=True)


def child_dying_with_its_parent_lets_the_lease_lapse(stack):
    """The Linux case on every platform: parent and child die together; the lease lapses and is recovered."""
    long_execution_keeps_its_lease_django(stack, kill_parent=True, child_dies_too=True)


def long_execution_keeps_its_lease_go(stack):
    """No switch, Go consumer: an email execution parked longer than one lease keeps it; its redelivery answers
    'active'; it then sends exactly once."""
    from TESTS.handoff.p3_scenarios_email import go_consumer_of, go_mail
    stack.job_handoff()
    to_go(stack)
    consumer = stack.go_consumer()
    go_consumer_of(stack)
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    go_mail(stack, task)
    stack.barrier.wait_arrived('claim:granted', task, 'Go consumer')
    try:
        outlive_the_first_lease(stack, task)
        check(probe_redelivery(stack, 'go', task) == 'active', 'its redelivery waits while the Go executor lives')
        stack.barrier.release('claim:granted', task)
        wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the parked Go execution completes')
    finally:
        stack.barrier.release('claim:granted', task)
        stack.stop(consumer)
    outcomes = [a['outcome'] for a in stack.attempts(task_id=task)]
    check(outcomes == ['completed'] and stack.smtp.count() == 1, f'one attempt, one send: {outcomes}, {stack.smtp.count()}')


def late_ack_redelivery_of_a_startup_backlog_message(stack):
    """The same loss for a message already queued when the worker starts (a restart with a backlog)."""
    late_ack_redelivery_after_worker_loss_runs_again(stack, backlog=True)


def late_ack_redelivery_through_a_transaction_pooler(stack):
    """The same loss with USE_PGBOUNCER=True (a supported deployment): a pooler keeps the server session of the dead
    child open, so whatever proves the executor alive must not live in a pooled session.

    The worker reaches the stand-in over TCP, so libpq first probes for GSSAPI encryption; on macOS that probe
    (pg_GSS_have_cred_cache) can hang forever in a forked prefork child. Deployments use Linux; the probe is
    disabled here (PGGSSENCMODE=disable) so the scenario tests the pooler, not the platform."""
    from TESTS.handoff.p3_stack import LingeringPooler
    pooler = LingeringPooler(stack.work / '.s.PGSQL.5432')
    try:
        late_ack_redelivery_after_worker_loss_runs_again(stack, worker_env={
            'USE_PGBOUNCER': 'True', 'PGBOUNCER_HOST': '127.0.0.1', 'PGBOUNCER_PORT': str(pooler.port),
            'PGGSSENCMODE': 'disable'})
    except ContractFailure as exc:
        raise ContractFailure(f'{exc} [pooler accepted {pooler.accepted}, connect errors {pooler.errors}]') from None


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
    # The target attempt carries the same task id, so it passes the same barrier key: lift the source's hold
    # once the source can no longer move (killed or stopped), or the target waits on it too.
    stack.barrier.release(arrival['point'], arrival['key'])
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
    # The target attempt carries the same task id, so it passes the same barrier key: lift the source's hold
    # once the source can no longer move (killed or stopped), or the target waits on it too.
    stack.barrier.release(arrival['point'], arrival['key'])
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
    stop_quiescent(stack, worker, GO_WORKER_APP, arrival)
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
    # The target attempt carries the same task id, so it passes the same barrier key: lift the source's hold
    # once the source can no longer move (killed or stopped), or the target waits on it too.
    stack.barrier.release(arrival['point'], arrival['key'])
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
    'late_ack_redelivery_after_worker_loss_runs_again': late_ack_redelivery_after_worker_loss_runs_again,
    'late_ack_redelivery_through_a_transaction_pooler': late_ack_redelivery_through_a_transaction_pooler,
    'late_ack_redelivery_of_a_startup_backlog_message': late_ack_redelivery_of_a_startup_backlog_message,
    'redelivery_while_the_executor_runs_waits': redelivery_while_the_executor_runs_waits,
    'long_execution_keeps_its_lease_django': long_execution_keeps_its_lease_django,
    'orphan_child_keeps_its_lease': orphan_child_keeps_its_lease,
    'child_dying_with_its_parent_lets_the_lease_lapse': child_dying_with_its_parent_lets_the_lease_lapse,
    'long_execution_keeps_its_lease_go': long_execution_keeps_its_lease_go,
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
