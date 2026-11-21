"""Matrix family handback: the four consumer hand-back reasons (BE_GO/internal/celeryworker/worker.go:
undecodable, unregistered, unsupported options, handler-level unsupported input) in exclusive Go mode.

Contract: an envelope the Go consumer cannot run never goes to the Django queue while Go is
effective. Its outcome is the one recorded in task-handoff-dispositions.json
handback_reasons[<reason>].outcome and is enforced here:
  blocked:<reason>   ledger state 'blocked' with block_reason <reason>, envelope preserved byte for byte
  terminal:<STATE>   Celery result <STATE> under the same task id (e.g. terminal:REVOKED), ledger terminal
  failure:<Class>    Celery result FAILURE whose exc_type is <Class> (the Django outcome for that input)
A supported input of the same task completes on Go (positive control).
"""
import json
import time
import uuid

from TESTS.handoff.p3_stack import DB_TASK, GO_QUEUE, check
from TESTS.handoff.p4_matrix_kit import await_done, exclusive_go, require_matrix_prerequisites

INPUTS = {
    'undecodable': lambda s, t: s.envelope(DB_TASK, t, queue=GO_QUEUE, stamp=time.time(),
                                           content_type='application/x-python-serialize'),
    'unregistered': lambda s, t: s.envelope('p4.unregistered.task', t, queue=GO_QUEUE, stamp=time.time()),
    'options': lambda s, t: s.envelope(DB_TASK, t, queue=GO_QUEUE, stamp=time.time(), chain=[{'task': DB_TASK}]),
    'input': lambda s, t: s.envelope('send_email_text', t, queue=GO_QUEUE, stamp=time.time(),
                                     args=['body', 'subject', 12345]),
}


def recorded_outcome(reason):
    from TESTS.handoff.test_p4_inventory import DISPOSITIONS
    check(DISPOSITIONS.exists(), 'contract: docs/architecture/go-migration/task-handoff-dispositions.json is missing '
                                 f'(handback_reasons.{reason})')
    outcome = json.loads(DISPOSITIONS.read_text()).get('handback_reasons', {}).get(reason, {}).get('outcome', '')
    check(outcome.split(':', 1)[0] in ('blocked', 'terminal', 'failure'), f'contract: handback_reasons.{reason}.outcome: {outcome!r}')
    return outcome


def handback(reason):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority('go', 1)
        stack.executors('go')
        task_id = str(uuid.uuid4())
        payload = INPUTS[reason](stack, task_id)
        name = json.loads(payload)['headers']['task']
        check(stack.admit('go', task_id, name, GO_QUEUE, payload) == 'accepted', 'admission')
        stack.push(GO_QUEUE, payload)
        state = await_done(stack, task_id, f'the Go consumer ({reason})')
        exclusive_go(stack, [task_id])
        kind, _, detail = recorded_outcome(reason).partition(':')
        row = stack.work_row(task_id)
        if kind == 'blocked':
            check((state, row['block_reason']) == ('blocked', detail), f'{reason}: {(state, row["block_reason"])}')
            check(bytes(row['envelope']) == payload, f'{reason}: the envelope is preserved byte for byte')
        elif kind == 'terminal':
            check(stack.task_result(task_id) == detail, f'{reason}: result {stack.task_result(task_id)}, recorded {detail}')
        else:
            from django_celery_results.models import TaskResult
            result = TaskResult.objects.filter(task_id=task_id).first()
            check(result and result.status == 'FAILURE' and json.loads(result.result).get('exc_type') == detail,
                  f'{reason}: expected FAILURE {detail}, got {result and (result.status, result.result)}')
    return scenario


def supported_input_finishes_on_go(stack):
    require_matrix_prerequisites(stack)
    stack.set_authority('go', 1)
    stack.executors('go')
    task_id = str(uuid.uuid4())
    payload = stack.envelope('send_email_text', task_id, queue=GO_QUEUE, stamp=time.time(),
                             args=['body', 'subject', ['member@example.test']])
    check(stack.admit('go', task_id, 'send_email_text', GO_QUEUE, payload) == 'accepted', 'admission')
    stack.push(GO_QUEUE, payload)
    check(await_done(stack, task_id, 'the Go consumer') == 'completed', 'a supported input completes on Go')
    exclusive_go(stack, [task_id])
    check(stack.smtp.count() == 1, f'one message accepted by SMTP, got {stack.smtp.count()}')
    recorded_outcome('input')


SCENARIOS = {f'{reason}_never_reaches_the_django_queue': handback(reason) for reason in INPUTS}
SCENARIOS['supported_input_finishes_on_go'] = supported_input_finishes_on_go
