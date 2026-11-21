"""Matrix family envelope_options: what task-coverage.md compares beyond task names (D06, D17) on the
Go executors, against the uninterrupted Django control.

Phase 3 already pins retry/child/expiry at the switch boundary (test_p3_broker_backlog d06_*). This
family adds, for the Go executors that now run every task in exclusive mode:
- rate limits: a task annotated in BE/core/celery.py task_annotations is started no faster on Go than
  on Django (Celery token bucket, capacity 1: change_type_of_stripe_payments '30/m' -> >= 2 s apart),
  also for work transferred at the switch;
- priority: the claim order of a mixed-priority backlog on the Go consumer equals the Django worker's
  for the same backlog (kombu Redis priority steps), and every message completes (no starvation);
- per-attempt identity: a failed send_mail_async attempt on Go is retried under the same task id with
  retries + 1, the same root/parent ids and the countdown Django uses (default_retry_delay * 2**retries);
- stale-restore stamps: a job published before the last completed restore ends FAILURE with exc_type
  StaleDataTask on Go exactly as CoordinatedTask does on Django;
- D17: a delayed job survives three round trips with its absolute due time (Go available_at / Celery
  eta) unchanged at every hop and runs once, after that time, on the final runtime; a job delayed by
  hours stays pending with the same due time after the round trips.
Soft/hard time limits (only instance.restore.execute: 3300/3600 s) are pinned by the P4-D record
(tasks[...].time_limits and go_time_limits), not by an hour-long run.
"""
from datetime import timedelta
import time
import uuid

from TESTS.handoff.p3_stack import DB_TASK, GO_QUEUE, MAIL_TASK, check, wait_for
from TESTS.handoff.p4_matrix_kit import (await_done, exclusive_go, now, produce_django, produce_go_job, produce_go_task,
                                          require_matrix_prerequisites, require_record)

FAMILY = 'envelope_options'
RATE_TASK = 'change_type_of_stripe_payments'


def claims(stack, task_ids):
    rows = []
    for task_id in task_ids:
        attempts = sorted(stack.attempts(task_id=task_id), key=lambda a: a['claimed_at'])
        check(attempts, f'{task_id} was never claimed')
        rows.append(attempts[0]['claimed_at'])
    return rows


def spacing(times):
    ordered = sorted(times)
    return [(b - a).total_seconds() for a, b in zip(ordered, ordered[1:])]


def paced(gaps, interval=2.0):
    """Celery's token bucket (capacity 1): after the first start, starts are >= interval apart; the first gap
    may be shorter because the first start is delayed by process startup, not by the bucket."""
    return len(gaps) >= 2 and all(gap >= interval - 0.1 for gap in sorted(gaps)[1:])


def rate_limit_spacing_matches_django(stack):
    require_matrix_prerequisites(stack)
    stack.executors('django')
    control = [produce_django(stack, RATE_TASK) for _ in range(4)]
    for task_id in control:
        await_done(stack, task_id, 'the Django worker')
    check(paced(spacing(claims(stack, control))), f'Django control spacing {spacing(claims(stack, control))}')
    workers = stack.processes['django']
    stack.processes['django'] = []
    queued = [produce_django(stack, RATE_TASK) for _ in range(2)]
    stack.switch('django', 1, 'go', stop=workers)
    stack.executors('go')
    fresh = [produce_go_job(stack, RATE_TASK) for _ in range(2)]
    for task_id in queued + fresh:
        check(await_done(stack, task_id, 'the Go worker') == 'completed', f'{task_id} on Go')
    gaps = spacing(claims(stack, queued + fresh))
    check(paced(gaps), f"Go starts {RATE_TASK} faster than its Django rate limit '30/m' allows: gaps {gaps}")
    exclusive_go(stack, queued + fresh)
    require_record(RATE_TASK, 'payments')


def priority_order_matches_django(stack):
    require_matrix_prerequisites(stack)
    orders = {}
    for runtime in ('django', 'go'):
        stack.reset()
        stack.set_authority(runtime, 1)
        queue = 'celery' if runtime == 'django' else GO_QUEUE
        ids = []
        for index, priority in enumerate((0, 9, 3, 9, 0, 6, 3, 6)):
            task_id = f'00000000-0000-4000-8000-{index:012d}'
            payload = stack.envelope('send_email_text', task_id, queue=queue, stamp=time.time(), priority=priority,
                                     args=[f'body {index}', f'subject {index}', [f'member{index}@example.test']])
            check(stack.admit(runtime, task_id, 'send_email_text', queue, payload) == 'accepted', 'admission')
            stack.push(queue, payload, priority)
            ids.append(task_id)
        stack.executors(runtime)
        for task_id in ids:
            check(await_done(stack, task_id, f'the {runtime} executor') == 'completed', f'{task_id} completes on {runtime}')
        orders[runtime] = [task for _, task in sorted(zip(claims(stack, ids), ids))]
        if runtime == 'go':
            exclusive_go(stack, ids)
    check(orders['go'] == orders['django'], f'claim order by priority differs: django {orders["django"]}, go {orders["go"]}')
    require_record('send_email_text', 'email')


def failed_attempt_retries_with_the_same_identity(stack):
    require_matrix_prerequisites(stack)
    observed = {}
    for runtime in ('django', 'go'):
        stack.reset()
        stack.set_authority(runtime, 1)
        stack.smtp.failing = True
        root, parent = str(uuid.uuid4()), str(uuid.uuid4())
        kwargs = dict(subject='Retry', message='body', from_email='noreply@example.test', recipient_list=['a@example.test'])
        if runtime == 'django':
            task_id = produce_django(stack, MAIL_TASK, kwargs=kwargs)
            root = task_id
        else:
            task_id = produce_go_task(stack, MAIL_TASK, kwargs=kwargs, root_id=root, parent_id=parent)
        stack.executors(runtime)
        row = wait_for(lambda: (lambda r: r if r and r['retries'] == 1 else None)(stack.work_row(task_id)),
                       f'{runtime}: the failed send was never retried under the same id', timeout=60)
        failed = sorted(stack.attempts(task_id=task_id), key=lambda a: a['claimed_at'])[0]
        delay = (row['eta'] - failed['claimed_at']).total_seconds() if row['eta'] else None
        observed[runtime] = {'state': row['state'], 'retries': row['retries'], 'delay': None if delay is None else round(delay)}
        # Celery's retry keeps the root and makes the failed attempt the parent (observed on the Django control).
        check((row['root_id'], row['parent_id']) == (root, task_id), f'{runtime}: retry lineage (root, parent) '
              f'{(row["root_id"], row["parent_id"])}, expected {(root, task_id)}')
        check(stack.smtp.count() == 0, f'{runtime}: nothing was accepted by the failing SMTP server')
        if runtime == 'go':
            exclusive_go(stack, [task_id])
    django, go = observed['django'], observed['go']
    check(go['retries'] == django['retries'] == 1 and go['state'] == django['state'],
          f'retry state differs: django {django}, go {go}')
    check(go['delay'] is not None and django['delay'] is not None and abs(go['delay'] - django['delay']) <= 2,
          f'retry countdown differs: django {django["delay"]} s, go {go["delay"]} s')
    require_record(MAIL_TASK, 'email')


def stale_restore_stamp_is_refused_on_go(stack):
    require_matrix_prerequisites(stack)
    outcomes = {}
    for runtime in ('django', 'go'):
        stack.reset()
        stack.set_authority(runtime, 1)
        stack.seed_balance()
        from instance.models import DataRestore
        from application.models import User
        DataRestore.objects.create(owner=User.original_objects.first(), association_id=uuid.uuid4(), state='completed',
                                   stage='completed', completed_at=now(), sha256='0' * 64)
        queue = 'celery' if runtime == 'django' else GO_QUEUE
        task_id = str(uuid.uuid4())
        payload = stack.envelope('send_email_text', task_id, queue=queue, stamp=time.time() - 60,
                                 args=['body', 'subject', ['member@example.test']])
        check(stack.admit(runtime, task_id, 'send_email_text', queue, payload) == 'accepted', 'admission')
        stack.push(queue, payload)
        stack.executors(runtime)
        state = await_done(stack, task_id, f'the {runtime} executor')
        from django_celery_results.models import TaskResult
        result = TaskResult.objects.filter(task_id=task_id).first()
        import json
        outcomes[runtime] = (state, result and result.status, result and json.loads(result.result or '{}').get('exc_type'),
                             stack.smtp.count())
        if runtime == 'go':
            exclusive_go(stack, [task_id])
    check(outcomes['django'][1:] == ('FAILURE', 'StaleDataTask', 0), f'Django control: {outcomes["django"]}')
    check(outcomes['go'] == outcomes['django'], f'stale-restore refusal differs: django {outcomes["django"]}, go {outcomes["go"]}')
    require_record('send_email_text', 'email')


def due_time(stack, task_id):
    row = stack.work_row(task_id)
    if row['transport'] == 'sql':
        job = stack.go_job(task_id)
        return job[7] if job else None
    return row['eta']


def delayed_job_survives_round_trips(stack):
    require_matrix_prerequisites(stack)
    stack.seed_balance()
    soon = produce_django(stack, DB_TASK, countdown=25)
    later = produce_django(stack, DB_TASK, countdown=3 * 3600)
    due = {task: stack.work_row(task)['eta'] for task in (soon, later)}
    check(all(due.values()), f'the Django producer records the absolute due times: {due}')
    generation, runtime = 1, 'django'
    for target in ('go', 'django', 'go'):
        generation = stack.switch(runtime, generation, target)
        runtime = target
        for task, expected in due.items():
            check(due_time(stack, task) == expected, f'after the hop to {target}: {task} due {due_time(stack, task)}, '
                                                      f'expected {expected}')
            check(stack.work_row(task)['state'] == 'pending', f'{task} is still pending after the hop to {target}')
    stack.executors('go')
    check(await_done(stack, soon, 'the Go worker at the due time', timeout=60) == 'completed', 'the delayed job runs')
    started = claims(stack, [soon])[0]
    check(started >= due[soon], f'the delayed job started at {started.isoformat()}, before its due time {due[soon].isoformat()}')
    check(stack.effects().get('assozeta-go-worker', 0) == 1, f'one execution on Go: {stack.effects()}')
    check(stack.work_row(later)['state'] == 'pending' and due_time(stack, later) == due[later],
          'the job due in three hours stays pending with its due time')
    exclusive_go(stack, [soon, later])
    require_record(DB_TASK, 'db_maintenance')


SCENARIOS = {
    'rate_limit_spacing_matches_django': rate_limit_spacing_matches_django,
    'priority_order_matches_django': priority_order_matches_django,
    'failed_attempt_retries_with_the_same_identity': failed_attempt_retries_with_the_same_identity,
    'stale_restore_stamp_is_refused_on_go': stale_restore_stamp_is_refused_on_go,
    'delayed_job_survives_round_trips': delayed_job_survives_round_trips,
}
