"""Matrix family print: print_document_compensation, print_document_customer_invoice, print_document_invoice,
print_document_medical_appointment, print_document_subscription.

task-coverage.md: use the destination API, renderer and storage; switch after queueing and after object
creation; check the stored document, linked record, content and duplicate-object policy; exercise access
rules and uncommon supported templates.

The print task's effect is one authenticated request to the trusted print endpoint of the ACTIVE API
(CURRENT_HOST = http://active-api:8000 since Phase 2); the API renders through the renderer and stores the
PDF. Here CURRENT_HOST is the stack's recording stub (p4_family_storage.Rig) for both runtimes, so each
scenario proves the request (method, path with query, Authorization passed through or absent) is made
exactly once by the executing runtime and never by the inactive one. Rendering, storage of the PDF, the
linked record and access rules of the endpoint itself are pinned by the Phase 2 renderer tests and the
document families (TESTS/contracts print/document families); this family does not re-render.

Scenarios (each ends with the identity oracle and the task's record):
- <task>__{django,go,queued_forward,queued_reverse} with the production argument shapes (invoice_generate.go
  and subscription_create.go publish positional [id, authorization(, send_receipt_email)]):
  subscription GET /document/subscription/<id>; invoice GET /document/invoice/<id>?send_receipt_email=False;
  customer invoice GET /document/einvoice/<id>; compensation GET /document/compensation/<id>; medical
  appointment GET /document/medical-appointment/<id> (token None: no Authorization header).
- print_document_invoice__keyword_send_receipt_email_{django,go}: kwargs {invoice_id, auth_token,
  send_receipt_email: true} -> ?send_receipt_email=True (uncommon supported shape).
- print_document_subscription__after_object_created_{forward,reverse}: the print request is an external
  effect (CONTRACT P3-B effects ledger, kind 'print', effect_key unique per job and retry): intent before
  the request, confirm after the endpoint answered; barrier effect:accepted (key = task id) between them.
  The source executor is killed there; settle marks the effect ambiguous and raises TransferBlocked
  ({'task_id', 'reason': 'ambiguous_effect'}); job_handoff.reconcile(store, task_id, 'sent', actor) resolves
  it; the switch completes and the target never repeats the request (no duplicate document).
"""
import uuid

from TESTS.handoff.p3_stack import ContractFailure, check
from TESTS.handoff.p4_family_storage import Case, rig, run, start
from TESTS.handoff.p4_matrix_kit import (await_done, identity, produce_django, produce_go_task, require_matrix_prerequisites,
                                          require_record)

FAMILY = 'print'
TOKEN = 'Bearer fixture-print-token'


def U(n):
    return str(uuid.UUID(int=0xC7000000 + n))


TASKS = {  # name: (args, expected path, expected Authorization)
    'print_document_subscription': ([U(101), TOKEN], f'/document/subscription/{U(101)}', TOKEN),
    'print_document_invoice': ([U(102), TOKEN], f'/document/invoice/{U(102)}?send_receipt_email=False', TOKEN),
    'print_document_customer_invoice': ([U(104), TOKEN], f'/document/einvoice/{U(104)}', TOKEN),
    'print_document_compensation': ([U(105), TOKEN], f'/document/compensation/{U(105)}', TOKEN),
    'print_document_medical_appointment': ([U(106), None], f'/document/medical-appointment/{U(106)}', None),
}


def seed(stack):
    from TESTS.handoff.p4_family_storage import seed_print_fixture
    ctx = seed_print_fixture(stack)
    rig(stack).take()
    return ctx


def verifier(path, authorization):
    def verify(stack, ctx, runtime, roots, kids):
        requests = rig(stack).seen()
        check([r['path'] for r in requests] == [path], f'{runtime}: print requests {[r["path"] for r in requests]}, expected [{path}]')
        check(requests[0]['authorization'] == authorization,
              f'{runtime}: Authorization {requests[0]["authorization"]!r}, expected {authorization!r}')
        check(not kids, f'{runtime}: the print task published children {kids}')
    return verify


def case(name, args=None, kwargs=None, path=None, authorization=None):
    spec_args, spec_path, spec_auth = TASKS[name]
    args = spec_args if args is None and kwargs is None else (args or [])
    return Case(name, seed, {'django': lambda s, c: [produce_django(s, name, args=args, kwargs=kwargs or {})],
                             'go': lambda s, c: [produce_go_task(s, name, args=args, kwargs=kwargs or {})]},
                verifier(path or spec_path, authorization if path else spec_auth))


def after_object_created(source, target):
    name = 'print_document_subscription'

    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        seed(stack)
        args, path, _ = TASKS[name]
        task = str(uuid.uuid4())
        stack.barrier.hold('effect:accepted', task)
        start(stack, source)
        if source == 'django':
            produce_django(stack, name, args=args, task_id=task)
        else:
            import time
            from TESTS.handoff.p3_stack import GO_QUEUE
            payload = stack.envelope(name, task, args=args, queue=GO_QUEUE, stamp=time.time())
            check(stack.admit('go', task, name, GO_QUEUE, payload) == 'accepted', 'admission')
            stack.push(GO_QUEUE, payload)
        arrival = stack.barrier.wait_arrived('effect:accepted', task, f'the {source} executor after the print endpoint '
                                                                      'answered (barrier effect:accepted)')
        check([r['path'] for r in rig(stack).seen()] == [path], 'the endpoint received the print request')
        victim = stack.processes[source][0] if source == 'django' else stack.processes['go'][-1]
        stack.kill_tree(victim, arrival)
        stack.barrier.reset()
        stack.processes[source] = []
        module = stack.job_handoff()
        record, steps = stack.freeze(source, 1)
        try:
            steps['settle'](record)
        except Exception as exc:
            blocked = getattr(module, 'TransferBlocked', None)
            check(blocked and isinstance(exc, blocked) and {'task_id': task, 'reason': 'ambiguous_effect'} in exc.blockers,
                  f'settle must block on the unconfirmed print effect: {exc!r}')
        else:
            raise ContractFailure('settle accepted a print attempt killed after the endpoint answered (ambiguous effect)')
        _, store, _ = stack.handoff_parts()
        module.reconcile(store, task, 'sent', 't-p4-fixture')
        generation = stack.finish_switch(record, steps)
        start(stack, target)
        check(stack.work_row(task)['state'] == 'completed', f'reconciled as sent: {stack.work_row(task)["state"]}')
        check(len(rig(stack).seen()) == 1, f'the target repeated the print request: {rig(stack).seen()}')
        identity(stack, {task: 'terminal:completed'}, (source, 1), (target, generation), {source: 0, target: 0})
        require_record(name, FAMILY)
    return scenario


def run_on(mode, c):
    return lambda stack: run(stack, c, FAMILY, mode)


SCENARIOS = {}
for _name in TASKS:
    for _label, _mode in (('django', 'django'), ('go', 'go'), ('queued_forward', 'forward'), ('queued_reverse', 'reverse')):
        SCENARIOS[f'{_name}__{_label}'] = run_on(_mode, case(_name))
_keyword = case('print_document_invoice', kwargs={'invoice_id': U(103), 'auth_token': TOKEN, 'send_receipt_email': True},
                path=f'/document/invoice/{U(103)}?send_receipt_email=True', authorization=TOKEN)
for _mode in ('django', 'go'):
    SCENARIOS[f'print_document_invoice__keyword_send_receipt_email_{_mode}'] = run_on(_mode, _keyword)
SCENARIOS['print_document_subscription__after_object_created_forward'] = after_object_created('django', 'go')
SCENARIOS['print_document_subscription__after_object_created_reverse'] = after_object_created('go', 'django')
