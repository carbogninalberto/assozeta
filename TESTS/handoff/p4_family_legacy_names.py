"""Matrix family legacy_names: auto_move_users_to_base_plan, nurturing_warm_leads, save_binary_to_storage,
send_updated_certificate_email -- absent from both Go registries.

task-coverage.md: for a live supported item provide an adapter; for a retired item prove retirement
and give already-accepted deliveries a documented outcome; otherwise refuse the transition while
preserving the item. The disposition is the implementer's, recorded in
task-handoff-dispositions.json legacy_names[<task>].disposition; these scenarios enforce that the
behaviour matches the record:
  adapter            the queued delivery from an older release completes on Go (ledger completed,
                     Celery result SUCCESS under the same id; the two tombstone tasks return
                     {'deprecated': true} like Django; save_binary_to_storage writes the object)
  retired            the transfer gives it the terminal outcome 'cancelled' with block_reason
                     'retired_task' (recorded, visible, never executed, never blocking)
  refuse_transition  preflight names it {'task_id', 'reason': 'retired_task'}, stage_backlog raises
                     TransferBlocked, the item stays byte-identical at the source and Django runs it
In every case nothing is handed back to a stopped runtime. Steady Django drains the same envelope.
"""
import json
import time
import uuid

from TESTS.handoff.p3_stack import ContractFailure, check, wait_for
from TESTS.handoff.p4_matrix_kit import (await_done, exclusive_go, require_matrix_prerequisites, require_record)

FAMILY = 'legacy_names'
LEGACY = {
    'auto_move_users_to_base_plan': ((), {'deprecated': True}),
    'nurturing_warm_leads': ((), {'deprecated': True}),
    'save_binary_to_storage': (('legacy/p4-binary.bin', 'cDQtbGVnYWN5LWJpbmFyeQ=='), None),
    'send_updated_certificate_email': (('00000000-0000-4000-8000-0000000000a4',), None),
}


def disposition(name):
    from TESTS.handoff.test_p4_inventory import DISPOSITIONS
    check(DISPOSITIONS.exists(), 'contract: docs/architecture/go-migration/task-handoff-dispositions.json is missing '
                                 f'(legacy_names.{name})')
    record = json.loads(DISPOSITIONS.read_text()).get('legacy_names', {}).get(name) or {}
    check(record.get('disposition') in ('adapter', 'retired', 'refuse_transition'),
          f'contract: legacy_names.{name}.disposition must be adapter|retired|refuse_transition: {record}')
    return record['disposition']


def legacy_envelope(stack, name):
    args, _ = LEGACY[name]
    task_id = str(uuid.uuid4())
    payload = stack.envelope(name, task_id, args=args, stamp=time.time() - 3600)
    stack.push('celery', payload)
    return task_id, payload


def steady_django(name):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        task_id, _ = legacy_envelope(stack, name)
        stack.executors('django')
        check(await_done(stack, task_id, 'the Django worker') == 'completed', f'Django drains the legacy {name}')
        expected = LEGACY[name][1]
        if expected is not None:
            from django_celery_results.models import TaskResult
            check(json.loads(TaskResult.objects.get(task_id=task_id).result) == expected, f'{name} tombstone result')
        require_record(name, FAMILY)
        disposition(name)
    return scenario


def queued_forward(name):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        decision = disposition(name)
        task_id, payload = legacy_envelope(stack, name)
        module = stack.job_handoff()
        blocked = getattr(module, 'TransferBlocked', ())
        if decision == 'refuse_transition':
            record = stack.record(uuid.uuid4(), 'django', 1, 'go')
            steps = stack.handoff_steps(heartbeat_grace=1.0)
            report = steps['preflight'](record)
            check({'task_id': task_id, 'reason': 'retired_task'} in report.get('blockers', []),
                  f'preflight must name the retired {name}: {report}')
            steps['close_admission'](record)
            steps['settle'](record)
            try:
                steps['stage_backlog'](record)
            except Exception as exc:
                check(blocked and isinstance(exc, blocked), f'stage_backlog must raise TransferBlocked for {name}: {exc!r}')
            else:
                raise ContractFailure(f'the transition with a queued retired {name} must be refused')
            row = stack.work_row(task_id)
            check(bytes(row['envelope']) == payload, f'{name} is preserved byte for byte')
            module.abort(*stack.handoff_parts()[1:], record)
            stack.executors('django')
            check(await_done(stack, task_id, 'the Django worker after the refused transition') == 'completed', name)
        else:
            stack.switch('django', 1, 'go')
            stack.executors('go')
            state = await_done(stack, task_id, 'the Go executors')
            row = stack.work_row(task_id)
            if decision == 'adapter':
                check(state == 'completed' and stack.task_result(task_id) == 'SUCCESS',
                      f'adapter: {name} completes on Go: {state}/{stack.task_result(task_id)}')
                expected = LEGACY[name][1]
                if expected is not None:
                    from django_celery_results.models import TaskResult
                    check(json.loads(TaskResult.objects.get(task_id=task_id).result) == expected,
                          f'adapter: {name} returns the Django tombstone')
            else:
                check((state, row['block_reason']) == ('cancelled', 'retired_task'),
                      f'retired: {name} gets the documented terminal outcome: {(state, row["block_reason"])}')
                check(not [a for a in stack.attempts(task_id=task_id) if a['outcome'] == 'completed'], f'{name} never ran')
            exclusive_go(stack, [task_id])
        require_record(name, FAMILY)
    return scenario


SCENARIOS = {}
for _name in LEGACY:
    SCENARIOS[f'{_name}__django'] = steady_django(_name)
    SCENARIOS[f'{_name}__queued_forward'] = queued_forward(_name)
