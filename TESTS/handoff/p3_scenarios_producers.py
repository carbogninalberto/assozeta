"""Phase 3 part B: every producer path is routed by the effective authority. Contract: test_p3_email_job.py docstring.

Django publication APIs (delay, apply_async, send_task, a signature, a chain, Celery Beat's
apply_entry) all reach the same admission; a Go task child (delete_users_with_request ->
send_email_text, the production Go producer wiring of cmd/worker) is admitted too.
"""
from datetime import date, datetime, timezone
import uuid

from TESTS.handoff import p3_oracle
from TESTS.handoff.p3_scenarios_db import REJECTED, heartbeats_after, new_id, to_go
from TESTS.handoff.p3_stack import DB_TASK, ContractFailure, check, wait_for


def django_paths(stack):
    """(name, callable(task_id)) for each Django publication API of the slice task."""
    from celery import chain
    from celery.beat import ScheduleEntry, Scheduler
    from application.tasks import reset_email_balance
    app = stack.app

    def beat(task_id):
        scheduler = Scheduler(app, lazy=True)
        entry = ScheduleEntry(name='handoff-beat', task=DB_TASK, schedule=60.0, app=app,
                              options={'task_id': task_id})
        scheduler.apply_entry(entry, producer=None)

    return [
        ('delay', lambda task_id: reset_email_balance.s().set(task_id=task_id).delay()),
        ('apply_async', lambda task_id: reset_email_balance.apply_async(task_id=task_id)),
        ('send_task', lambda task_id: app.send_task(DB_TASK, task_id=task_id)),
        ('signature', lambda task_id: reset_email_balance.si().apply_async(task_id=task_id)),
        ('chain', lambda task_id: chain(reset_email_balance.si().set(task_id=task_id), reset_email_balance.si()).apply_async()),
        ('beat', beat),
    ]


def run_path(stack, call, task_id):
    try:
        call(task_id)
    except Exception as exc:
        return 'raised', f'{type(exc).__module__}.{type(exc).__name__}'
    return 'returned', None


def django_paths_stale_after_commit(stack):
    stack.commit_authority(str(uuid.uuid4()), 'django', 1, 'go')
    for name, call in django_paths(stack):
        task = new_id()
        outcome = run_path(stack, call, task)
        copies = {key: values for key, values in stack.redis_copies(task).items()}
        check(not copies and not any(stack.redis.lrange('celery', 0, -1)),
              f'stale Django {name} published after the authority moved to go ({sorted(copies) or "celery"})')
        if name != 'beat':  # Beat logs a failed publication and keeps its schedule; nothing may be published
            check(outcome == ('raised', REJECTED), f'stale Django {name} must raise {REJECTED}; got {outcome}')
        check(stack.work_row(task) is None, f'rejected {name} left a ledger row')


def django_paths_held_while_frozen(stack):
    stack.job_handoff()
    operation = uuid.uuid4()
    record = stack.record(operation, 'django', 1, 'go')
    stack.handoff_steps(heartbeat_grace=1.0)['close_admission'](record)
    for name, call in django_paths(stack):
        task = new_id()
        outcome = run_path(stack, call, task)
        check(outcome == ('returned', None), f'Django {name} during frozen intake must succeed durably; got {outcome}')
        row = stack.work_row(task)
        check(row and row['state'] == 'held' and str(row['operation_id']) == str(operation),
              f'Django {name} during frozen intake must be held by the operation: {row}')
        check(not stack.redis_copies(task), f'Django {name} published to the broker during frozen intake')


def seed_deletion_request(stack):
    from application.models import User
    user = uuid.UUID(int=0xA3000401)
    User.original_objects.bulk_create([User(user_id=user, username='handoff-leaving', role=1, password='!private-fixture',
                                            email='leaving@example.test', is_active=True, delete_on=date.today())])
    return user


def go_task_child_is_admitted(stack):
    """A Go durable job (delete_users_with_request) publishes its send_email_text child during frozen intake."""
    stack.job_handoff()
    to_go(stack)
    seed_deletion_request(stack)
    worker = stack.go_worker()
    job = new_id()
    stack.barrier.hold('claim:granted', job)
    check(stack.go_enqueue(job, kind='delete_users_with_request').get('outcome') == 'enqueued', 'Go enqueue')
    stack.barrier.wait_arrived('claim:granted', job, 'Go durable worker')
    operation = uuid.uuid4()
    record = stack.record(operation, 'go', 2, 'django')
    steps = stack.handoff_steps(heartbeat_grace=1.0)
    steps['close_admission'](record)
    stack.barrier.release('claim:granted', job)
    wait_for(lambda: (stack.go_job(job) or ('',))[0] == 'completed', 'the running Go job finishes on the source')
    children = [row[0] for row in stack.sql("SELECT task_id FROM public.assozeta_handoff_work WHERE task_name='send_email_text'")]
    check(len(children) == 1, f'the Go child publication reached the ledger exactly once: {children}')
    child = stack.work_row(children[0])
    check(child['state'] == 'held' and child['origin_runtime'] == 'go' and not stack.redis_copies(child['task_id']),
          f'a Go child published during frozen intake is held, not queued: {child["state"]}, {stack.redis_copies(child["task_id"])}')
    steps['settle'](record)
    stack.stop(worker)
    steps['stage_backlog'](record)
    check(tuple(stack.commit_authority(str(operation), 'go', 2, 'django')) == ('committed', 'django', 3), 'commit')
    steps['activate'](record)
    django = stack.django_worker()
    wait_for(lambda: stack.task_result(child['task_id']) == 'SUCCESS', 'the Go child runs once on Django')
    stack.stop(django)
    check(stack.smtp.count() == 1, f'SMTP accepted {stack.smtp.count()}; the child notice is sent once')
    p3_oracle.check(stack, accepted={job: 'source_completed'}, children={child['task_id']: 'target_completed'},
                    source=('go', 2), target=('django', 3), effects={'django': 0, 'go': 0},
                    completions={'go': 1, 'django': 1})


SCENARIOS = {
    'django_paths_stale_after_commit': django_paths_stale_after_commit,
    'django_paths_held_while_frozen': django_paths_held_while_frozen,
    'go_task_child_is_admitted': go_task_child_is_admitted,
}
assert datetime and timezone and heartbeats_after and ContractFailure
