"""Phase 3 part B, slice 2: an email task (``core.tasks.send_mail_async``) on both broker consumers.

External effects are counted by the stack's CountingSMTP (accepted messages). Contract: the
docstring of TESTS/handoff/test_p3_email_job.py.
"""
import json
import signal
import time
import uuid

from TESTS.handoff import p3_oracle
from TESTS.handoff.p3_scenarios_db import claims_since, consumer_of, heartbeats_after, new_id, to_go
from TESTS.handoff.p3_stack import GO_QUEUE, MAIL_TASK, ContractFailure, check, wait_for

SENDER = 'Handoff Fixture <noreply@example.test>'


def mail_kwargs(recipient, **extra):
    return {'subject': 'Conferma iscrizione', 'message': 'Ciao,\nla tua iscrizione è confermata.\n',
            'from_email': SENDER, 'recipient_list': [recipient], **extra}


def publish_mail(stack, task_id, recipient='socio@example.test', **options):
    """Django producer path: send_mail_async.apply_async."""
    from core.tasks import send_mail_async
    kwargs = options.pop('kwargs', None) or mail_kwargs(recipient)
    try:
        send_mail_async.apply_async(kwargs=kwargs, task_id=task_id, **options)
    except Exception as exc:
        return 'raised', f'{type(exc).__module__}.{type(exc).__name__}'
    return 'returned', None


def go_mail(stack, task_id, recipient='socio@example.test', queue=GO_QUEUE, priority=0, **envelope):
    """Go-accepted broker work: a correct Go producer admits durably, then publishes (contract P3-A)."""
    payload = stack.envelope(MAIL_TASK, task_id, kwargs=mail_kwargs(recipient), queue=queue, priority=priority,
                             stamp=time.time(), **envelope)
    outcome = stack.admit('go', task_id, MAIL_TASK, queue, payload)
    check(outcome == 'accepted', f'Go admission of {task_id} while Go owns: {outcome}')
    stack.push(queue, payload, priority)
    return payload


def handoff(stack, source, generation, target, *, steps=None, **options):
    operation = uuid.uuid4()
    record = stack.record(operation, source, generation, target)
    options.setdefault('heartbeat_grace', 1.0)
    steps = steps or stack.handoff_steps(**options)
    summary = {'close_admission': steps['close_admission'](record), 'settle': steps['settle'](record),
               'stage_backlog': steps['stage_backlog'](record)}
    check(tuple(stack.commit_authority(str(operation), source, generation, target)) == ('committed', target, generation + 1),
          'authority commit')
    summary['activate'] = steps['activate'](record)
    return record, summary


def blocked(stack, step, record, expected):
    """Run a step that must raise job_handoff.TransferBlocked listing exactly the expected (task_id, reason)."""
    module = stack.job_handoff()
    check(hasattr(module, 'TransferBlocked'), 'contract: job_handoff.TransferBlocked is missing')
    try:
        result = step(record)
    except module.TransferBlocked as exc:
        found = sorted((item['task_id'], item['reason']) for item in exc.blockers)
        check(found == sorted(expected), f'TransferBlocked lists {found}; expected {sorted(expected)}')
        for item in exc.blockers:
            check(set(item) >= {'task_id', 'reason'} and not ({'args', 'kwargs', 'envelope', 'body'} & set(item)),
                  f'a blocker must be an opaque reference and a reason, never task arguments: {sorted(item)}')
        return exc
    raise ContractFailure(f'expected TransferBlocked {sorted(expected)}; the step returned {result}')


def reconcile(stack, task_id, decision):
    module, store, _ = stack.handoff_parts()
    check(hasattr(module, 'reconcile'), 'contract: job_handoff.reconcile(store, task_id, decision, actor) is missing')
    return stack.contract_call('reconcile', module.reconcile, module)(store, task_id, decision,
                                                               actor='00000000-0000-4000-8000-000000000001')


def newest_consumer(stack, runtime):
    return max(consumer_of(stack, runtime), key=lambda row: row['registered_at'])['consumer_id']


def go_consumer_of(stack):
    rows = consumer_of(stack, 'go')
    brokers = [row for row in rows if row['role'] == 'broker']
    check(brokers, f'the Go consumer must register with role broker: {rows}')
    return brokers[0]['consumer_id']


# -- transfers that run once ----------------------------------------------------------------------
def forward_runs_once_on_go_consumer(stack):
    stack.job_handoff()
    task = new_id()
    check(publish_mail(stack, task) == ('returned', None), 'Django publication')
    check(list(stack.redis_copies(task)) == ['celery'], f'the Django job is queued on celery: {stack.redis_copies(task)}')
    accepted_at = stack.work_row(task)['accepted_at']
    handoff(stack, 'django', 1, 'go')
    queued = stack.redis.lrange(GO_QUEUE, 0, -1)
    check(stack.queued_ids(GO_QUEUE) == [task], f'activation publishes the job once on {GO_QUEUE}: {stack.queued_ids(GO_QUEUE)}')
    message = json.loads(queued[0])
    check(message['properties']['delivery_info']['routing_key'] == GO_QUEUE and message['headers']['task'] == MAIL_TASK
          and abs(message['headers']['assozeta_published_at'] - accepted_at.timestamp()) < 1e-3,
          f'the Go consumer copy keeps id, task and restore stamp, routed to {GO_QUEUE}: {message["headers"]}')
    consumer = stack.go_consumer()
    wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the transferred email runs on the Go consumer')
    stack.stop(consumer)
    check(stack.smtp.count() == 1, f'SMTP accepted {stack.smtp.count()} messages; expected exactly 1')
    check(stack.smtp.intents_at_accept == [1], 'the Go consumer must record the effect intent before the SMTP '
          f'transaction (unconfirmed intents seen by the provider at acceptance: {stack.smtp.intents_at_accept})')
    effects = stack.effects_rows(task)
    check([e['state'] for e in effects] == ['confirmed'], f'the provider acceptance is a confirmed effect: {effects}')
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 0}, completions={'django': 0, 'go': 1})


def reverse_runs_once_on_django(stack):
    stack.job_handoff()
    to_go(stack)
    task = new_id()
    go_mail(stack, task)
    handoff(stack, 'go', 2, 'django')
    check(stack.queued_ids('celery') == [task] and not stack.redis.llen(GO_QUEUE),
          f'activation moves the job from {GO_QUEUE} to celery once: {stack.queued_ids("celery")}')
    worker = stack.django_worker()
    wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the transferred email runs on Django')
    stack.stop(worker)
    check(stack.smtp.count() == 1, f'SMTP accepted {stack.smtp.count()} messages; expected exactly 1')
    check(stack.smtp.intents_at_accept == [1], 'Django must record the effect intent before the SMTP transaction '
          f'(unconfirmed intents seen by the provider at acceptance: {stack.smtp.intents_at_accept})')
    check([e['state'] for e in stack.effects_rows(task)] == ['confirmed'], 'the Django send is a confirmed effect')
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('go', 2), target=('django', 3),
                    effects={'django': 0, 'go': 0}, completions={'django': 1, 'go': 0})


# -- fencing of the Go broker consumer ------------------------------------------------------------
def stale_go_consumer_cannot_claim(stack):
    """Django owns; a Go consumer started from an old configuration finds a message on its queue."""
    task = new_id()
    stack.push(GO_QUEUE, stack.envelope(MAIL_TASK, task, kwargs=mail_kwargs('socio@example.test'), queue=GO_QUEUE,
                                        stamp=time.time()))
    consumer = stack.go_consumer()

    def evidence():
        if stack.smtp.count():
            raise ContractFailure(f'stale Go consumer sent {task} while Django owns the instance')
        try:
            return consumer_of(stack, 'go', timeout=0.01)
        except ContractFailure:
            return None
    wait_for(evidence, 'the stale Go consumer never registered a heartbeat (contract: --handoff-heartbeat)', timeout=30)
    heartbeats_after(stack, go_consumer_of(stack))
    check(stack.smtp.count() == 0 and stack.task_result(task) is None, 'a stale Go consumer executed work')
    stack.stop(consumer)


def go_consumer_freeze_ack_zero_claims(stack):
    stack.job_handoff()
    to_go(stack)
    consumer = stack.go_consumer()
    consumer_id = go_consumer_of(stack)
    operation = uuid.uuid4()
    record = stack.record(operation, 'go', 2, 'django')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    row = next(r for r in stack.consumers('go') if r['consumer_id'] == consumer_id)
    check(str(row['freeze_operation_id']) == str(operation) and row['frozen_at'],
          f'close_admission returned before the Go consumer acknowledged: {row}')
    legacy = new_id()
    stack.push(GO_QUEUE, stack.envelope(MAIL_TASK, legacy, kwargs=mail_kwargs('socio@example.test'), queue=GO_QUEUE,
                                        stamp=time.time()))
    held = new_id()
    payload = stack.envelope(MAIL_TASK, held, kwargs=mail_kwargs('socio@example.test'), queue=GO_QUEUE, stamp=time.time())
    check(stack.admit('go', held, MAIL_TASK, GO_QUEUE, payload) == 'held', 'Go admission during the freeze is held')
    heartbeats_after(stack, consumer_id)
    check(GO_QUEUE in stack.redis_copies(legacy), 'the frozen Go consumer took a message after its freeze acknowledgement')
    check(not claims_since(stack, consumer_id, row['frozen_at']) and stack.smtp.count() == 0,
          'the frozen Go consumer claimed or sent after its freeze acknowledgement')
    stack.stop(consumer)


# -- external effects: confirmed, safe retry, ambiguous (D09) --------------------------------------
def crash_before_send_is_a_safe_retry(stack):
    stack.job_handoff()
    worker = stack.django_worker()
    stack.django_ready()
    task = new_id()
    stack.barrier.hold('claim:granted', task)
    check(publish_mail(stack, task) == ('returned', None), 'Django publication')
    arrival = stack.barrier.wait_arrived('claim:granted', task, 'Django worker')
    stack.kill_tree(worker, arrival)
    # The target attempt carries the same task id, so it passes the same barrier key: lift the source's hold
    # once the source can no longer move (killed or stopped), or the target waits on it too.
    stack.barrier.release(arrival['point'], arrival['key'])
    check(stack.smtp.count() == 0, 'nothing was sent before the crash')
    handoff(stack, 'django', 1, 'go')
    consumer = stack.go_consumer()
    wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the safely retried email runs on the Go consumer')
    stack.stop(consumer)
    check(stack.smtp.count() == 1, f'SMTP accepted {stack.smtp.count()}; a safe retry sends exactly once')
    outcomes = sorted((a['runtime'], a['outcome']) for a in stack.attempts(task_id=task))
    check(outcomes == [('django', 'released'), ('go', 'completed')],
          f'the crashed attempt is released (safe retry) and the target attempt completes: {outcomes}')
    check([e['state'] for e in stack.effects_rows(task)] == ['confirmed'], 'one confirmed effect')


def ambiguous_send_blocks_until_reconciled_forward(stack):
    """D09, Django source: SMTP accepted, the worker dies before recording the outcome."""
    module = stack.job_handoff()
    for name in ('abort', 'reconcile', 'TransferBlocked'):
        check(hasattr(module, name), f'contract: job_handoff.{name} is missing')
    worker = stack.django_worker()
    stack.django_ready()
    task = new_id()
    stack.barrier.hold('effect:accepted', task)
    check(publish_mail(stack, task) == ('returned', None), 'Django publication')
    arrival = stack.barrier.wait_arrived('effect:accepted', task, 'Django worker (provider accepted)')
    check(stack.smtp.count() == 1, 'the provider accepted the message before the crash')
    stack.kill_tree(worker, arrival)
    # The target attempt carries the same task id, so it passes the same barrier key: lift the source's hold
    # once the source can no longer move (killed or stopped), or the target waits on it too.
    stack.barrier.release(arrival['point'], arrival['key'])
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    steps['settle'](record)
    effects = stack.effects_rows(task)
    check([e['state'] for e in effects] == ['ambiguous'], f'an unconfirmed provider effect becomes ambiguous: {effects}')
    row = stack.work_row(task)
    check((row['state'], row['block_reason']) == ('blocked', 'ambiguous_effect'), f'the job is blocked: {row}')
    blocked(stack, steps['stage_backlog'], record, [(task, 'ambiguous_effect')])
    _, store, broker = stack.handoff_parts()
    stack.contract_call('abort', module.abort, module)(store, broker, record)
    check(stack.sql('SELECT runtime, generation, admission FROM public.assozeta_go_job_ownership')[0] == ('django', 1, 'open'),
          'abort before commit reopens the source at the same generation')
    restarted = stack.django_worker()
    stack.django_ready()
    stack.redis.lpush('celery', bytes(stack.work_row(task)['envelope']))
    heartbeats_after(stack, newest_consumer(stack, 'django'))
    check(stack.smtp.count() == 1, 'the restarted source replayed an ambiguous send automatically')
    stack.stop(restarted)
    reconcile(stack, task, 'sent')
    check([e['state'] for e in stack.effects_rows(task)] == ['reconciled_sent'] and stack.work_row(task)['state'] == 'completed',
          'reconciliation "sent" records the decision and completes the job without a send')
    handoff(stack, 'django', 1, 'go')
    check(stack.smtp.count() == 1, f'SMTP accepted {stack.smtp.count()}; expected exactly 1 after reconciliation')
    p3_oracle.check(stack, accepted={task: 'terminal:completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 0, 'go': 0}, completions={'django': 0, 'go': 0})


def ambiguous_send_blocks_until_reconciled_reverse(stack):
    """D09, Go source: the Go consumer dies after SMTP accepted; the owner decides 'not_sent' (replay once)."""
    stack.job_handoff()
    to_go(stack)
    consumer = stack.go_consumer()
    go_consumer_of(stack)
    task = new_id()
    stack.barrier.hold('effect:accepted', task)
    go_mail(stack, task)
    arrival = stack.barrier.wait_arrived('effect:accepted', task, 'Go consumer (provider accepted)')
    check(stack.smtp.count() == 1, 'the provider accepted the message before the crash')
    stack.kill_tree(consumer, arrival)
    # The target attempt carries the same task id, so it passes the same barrier key: lift the source's hold
    # once the source can no longer move (killed or stopped), or the target waits on it too.
    stack.barrier.release(arrival['point'], arrival['key'])
    operation = uuid.uuid4()
    record = stack.record(operation, 'go', 2, 'django')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    steps['settle'](record)
    check([e['state'] for e in stack.effects_rows(task)] == ['ambiguous'], 'the Go send is ambiguous')
    blocked(stack, steps['stage_backlog'], record, [(task, 'ambiguous_effect')])
    reconcile(stack, task, 'not_sent')
    check(stack.work_row(task)['state'] == 'pending', 'reconciliation "not_sent" makes the job runnable once more')
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'go', 2, 'django')) == ('committed', 'django', 3), 'commit')
    steps['activate'](record)
    worker = stack.django_worker()
    wait_for(lambda: stack.task_result(task) == 'SUCCESS', 'the reconciled job runs once on Django')
    stack.stop(worker)
    states = sorted(e['state'] for e in stack.effects_rows(task))
    check(states == ['confirmed', 'reconciled_unsent'], f'the decision and the replay are both recorded: {states}')
    check(stack.smtp.count() == 2, f'SMTP accepted {stack.smtp.count()}: the ambiguous send plus the owner-approved replay')
    p3_oracle.check(stack, accepted={task: 'target_completed'}, source=('go', 2), target=('django', 3),
                    effects={'django': 0, 'go': 0}, completions={'django': 1, 'go': 0})


SCENARIOS = {
    'forward_runs_once_on_go_consumer': forward_runs_once_on_go_consumer,
    'reverse_runs_once_on_django': reverse_runs_once_on_django,
    'stale_go_consumer_cannot_claim': stale_go_consumer_cannot_claim,
    'go_consumer_freeze_ack_zero_claims': go_consumer_freeze_ack_zero_claims,
    'crash_before_send_is_a_safe_retry': crash_before_send_is_a_safe_retry,
    'ambiguous_forward': ambiguous_send_blocks_until_reconciled_forward,
    'ambiguous_reverse': ambiguous_send_blocks_until_reconciled_reverse,
}
assert signal
