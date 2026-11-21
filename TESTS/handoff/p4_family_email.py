"""Matrix family email: core.tasks.send_mail_async, send_email_text, send_email_template, send_email_html.

task-coverage.md row: test child sends, SMTP failures/retries, attachments and a crash immediately
after SMTP accepts a message; retain result ids and prevent an automatic second send on an
ambiguous result. External effects are counted at the stack's CountingSMTP (accepted messages,
recipients parsed from the To: header), EmailLog rows (recipient, result) and the Phase 3 effects
ledger (assozeta_handoff_effects, CONTRACT P3-B).

Producers: Django ``Task.apply_async(kwargs=...)``; Go: the admitted protocol-2 envelope on
'assozeta-go' with the keyword arguments the Go callers publish (celery.Task{Kwargs: ...}, e.g.
service/user_deletion_requests.go for send_email_text, communications_send_email.go for
send_mail_async). No association SMTP configuration is seeded: every send goes through the
instance/system SMTP (the CountingSMTP), so the business-effect trigger stays at zero.

Scenarios (per task: __django, __go, __queued_forward, __queued_reverse from p4_matrix_kit.standard):
  every task sends exactly one message to socio@example.test, the three send_email_* tasks through
  one child core.tasks.send_mail_async whose ledger row carries parent_id = root_id = the parent id
  (the oracle accounts for it), and EmailLog records (socio@example.test, 'Sent').
  send_email_text__after_child_published_forward/_reverse: the parent completes on the source, its
    child is published but not yet claimed when the freeze closes admission; the child runs once on
    the target. Mechanism: hold barrier reservation:confirmed for 'all' with release.<parent id>
    (the parent passes, the child is held before its claim), wait for the freeze to be durable
    (ownership admission 'frozen'), then release the child: its claim is refused (frozen), the
    ledger keeps it pending and stage_backlog/activate move it.
  send_mail_async__smtp_retry_round_trip: SMTP refuses MAIL FROM (stack.smtp.failing); the Django
    task retries with its countdown (Celery retry admitted in place, retries 1, eta in the future);
    after Django -> Go and Go -> Django the row keeps retries 1 and its eta (to the millisecond),
    never runs early and SMTP stays at zero.
  send_mail_async__crash_after_smtp_accept_django / _go: the executor is SIGKILLed at barrier
    effect:accepted (key = task id: after SMTP accepted, before the confirmation). The restarted
    executor of the same runtime never sends again; the effect is ambiguous and the job blocked
    (reason ambiguous_effect) until job_handoff.reconcile(store, id, 'sent', actor) completes it;
    exactly one message is accepted overall. The Phase 3 tests cover the same crash across a switch.

New contract names pinned here (besides P3/P4-C):
  - Children published by a running job carry parent_id = the parent's task id and root_id = the
    parent's root (Celery semantics) on both runtimes, so the ledger links them.
  - Routing by the effective authority: in Go mode a child whose task has a Go consumer handler is
    published to 'assozeta-go' whatever GO_TASK_QUEUE_TASKS says; nothing reaches 'celery'.
"""
import email
from email.utils import getaddresses
import threading
import time
import uuid

from TESTS.handoff.p3_stack import GO_QUEUE, MAIL_TASK, ContractFailure, check, wait_for
from TESTS.handoff.p4_matrix_kit import (Case, await_done, children_of, exclusive_go, produce_django, produce_go_task,
                                          require_matrix_prerequisites, require_record, standard)

FAMILY = 'email'
SENDER = 'Handoff Fixture <noreply@example.test>'
TO = 'socio@example.test'
TEXT, TEMPLATE, HTML = 'send_email_text', 'send_email_template', 'send_email_html'
NEW_SUBSCRIPTION = 'email/account/email_new_subscription.html'
TERMINAL = ('completed', 'failed', 'cancelled', 'expired', 'revoked', 'blocked')


# -- shared helpers (also used by p4_family_certificate_emails and p4_family_backup_notify) ------------
def recipients(stack):
    """Lower-cased To: addresses of every message the CountingSMTP accepted, in acceptance order."""
    found = []
    for body in stack.smtp.messages():
        message = email.message_from_string(body.replace('\r\n', '\n'))
        found += [address.lower() for _, address in getaddresses(message.get_all('To', []))]
    return found


def await_smtp(stack, count, who, timeout=90):
    wait_for(lambda: stack.smtp.count() >= count, f'{who}: SMTP accepted {stack.smtp.count()} of {count} messages',
             timeout=timeout)


def await_children(stack, parents, count, who, timeout=90):
    """Wait until `count` children of `parents` exist and every one is terminal; returns their ids."""
    def ready():
        kids = [c for p in parents for c in children_of(stack, p)]
        if len(kids) < count:
            return None
        rows = [stack.work_row(k) for k in kids]
        return kids if all(r and r['state'] in TERMINAL for r in rows) else None
    return wait_for(ready, f'{who}: {count} child job(s) of {parents} never finished '
                           f'(found {[c for p in parents for c in children_of(stack, p)]})', timeout=timeout)


def switch_running(stack, source, generation, target):
    """Switch while the source executors run (they acknowledge the freeze), stopping them after settle."""
    processes, stack.processes[source] = list(stack.processes[source]), []
    return stack.switch(source, generation, target, stop=processes)


def mark(context, key, runtime):
    context.setdefault(key, runtime)


def child_expectations(stack, context):
    """Children completed by the executing runtime: source_completed when it also produced, else target."""
    expectation = 'source_completed' if context.get('produced_by') == context.get('executed_by') else 'target_completed'
    return {child: expectation for parent in context.get('tasks', []) for child in children_of(stack, parent)}


def reset_people(stack):
    """Remove the domain rows the e-mail families seed (fixed ids, CASCADE like the email adapters)."""
    stack.sql('''TRUNCATE auditlog_logentry, application_auditlogindex, bakney_user_partial, application_reminders,
      application_instructor, application_associatetutorrelation, application_subscription, application_medicalcertificate,
      application_associate, communications_communicationconfiguration, application_sportassociation,
      instance_instanceconfiguration, application_emaillog, bakney_user RESTART IDENTITY CASCADE''')
    stack.sql('TRUNCATE handoff_test_effects')
    # core.settings derives DEFAULT_TEAM_EMAIL from EMAIL_HOST_USER at import; the P3 settings then clear the
    # login user, so a worker started with this value has a valid team sender and still sends without AUTH.
    stack.env['EMAIL_HOST_USER'] = 'noreply@example.test'


def producer(task, kwargs_of):
    """{'django': ..., 'go': ...} publishing one `task` with kwargs_of(context); records ids and producer."""
    def django(stack, context):
        context['produced_by'] = 'django'
        context['tasks'] = [produce_django(stack, task, kwargs=kwargs_of(context))]
        return context['tasks']

    def go(stack, context):
        context['produced_by'] = 'go'
        context['tasks'] = [produce_go_task(stack, task, kwargs=kwargs_of(context))]
        return context['tasks']
    return {'django': django, 'go': go}


# -- the four tasks ---------------------------------------------------------------------------------
def seed(stack):
    reset_people(stack)
    return {}


def mail_kwargs(context):
    return {'subject': 'Conferma iscrizione', 'message': 'Ciao,\nla tua iscrizione è confermata.\n', 'from_email': SENDER,
            'recipient_list': [TO]}


KWARGS = {
    MAIL_TASK: mail_kwargs,
    TEXT: lambda c: {'message': 'Il tuo account è stato eliminato.', 'subject': 'Handoff | Account eliminato',
                     'recipient_list': [TO]},
    TEMPLATE: lambda c: {'recipient_list': [TO], 'subject': 'Ricordati di approvare', 'template': NEW_SUBSCRIPTION,
                         'data': {'athlete_first_name': 'Giulia', 'athlete_last_name': 'Rossi',
                                  'sport_association': {'denomination': 'ASD Uno'}, 'app_host': 'https://app.example.test',
                                  'settings': {'WHITELABEL_NAME': 'Handoff Fixture', 'IS_WHITELABEL': False}}},
    HTML: lambda c: {'html_message': '<p>Promemoria</p>', 'subject': 'Handoff | Promemoria', 'recipient_list': [TO]},
}


def verify_for(task):
    def verify(stack, context, runtime):
        context['executed_by'] = runtime
        await_smtp(stack, 1, f'{task} on {runtime}')
        if task != MAIL_TASK:
            kids = await_children(stack, context['tasks'], 1, f'{task} on {runtime}')
            names = sorted(stack.work_row(k)['task_name'] for k in kids)
            check(names == [MAIL_TASK], f'{task} on {runtime}: children {names}, expected one {MAIL_TASK}')
            for kid in kids:
                check(stack.work_row(kid)['state'] == 'completed', f'{task} on {runtime}: child {kid} {stack.work_row(kid)["state"]}')
            if runtime == 'go':
                exclusive_go(stack, kids)
        check(stack.smtp.count() == 1 and recipients(stack) == [TO],
              f'{task} on {runtime}: SMTP accepted {recipients(stack)}, expected exactly [{TO!r}]')
        logs = sorted((r.lower(), result) for r, result in stack.email_logs())
        check(logs == [(TO, 'Sent')], f'{task} on {runtime}: EmailLog {logs}, expected [({TO!r}, "Sent")]')
    return verify


CASES = {task: Case(task, seed, producer(task, KWARGS[task]), verify_for(task),
                    children=None if task == MAIL_TASK else child_expectations) for task in KWARGS}


# -- after_child_published ------------------------------------------------------------------------------
def frozen(stack):
    return stack.sql('SELECT admission FROM public.assozeta_go_job_ownership WHERE singleton=1')[0][0] == 'frozen'


def after_child_published(source, target):
    case = CASES[TEXT]

    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        context = case.seed(stack)
        parent = str(uuid.uuid4())
        stack.barrier.hold('reservation:confirmed', 'all')
        stack.barrier.release('reservation:confirmed', parent)
        stack.executors(source)
        context['produced_by'] = source
        if source == 'django':
            produce_django(stack, TEXT, kwargs=KWARGS[TEXT](context), task_id=parent)
        else:
            payload = stack.envelope(TEXT, parent, kwargs=KWARGS[TEXT](context), queue=GO_QUEUE, stamp=time.time())
            check(stack.admit('go', parent, TEXT, GO_QUEUE, payload) == 'accepted', 'Go admission of the parent')
            stack.push(GO_QUEUE, payload)
        context['tasks'] = [parent]
        check(await_done(stack, parent, f'the {source} executor (parent)') == 'completed', 'the parent completes on the source')
        child = wait_for(lambda: children_of(stack, parent), f'the parent on {source} published no child linked by '
                                                             'parent_id/root_id (contract: child lineage)')[0]
        stack.barrier.wait_arrived('reservation:confirmed', child, f'the {source} executor reserving the child')
        result, failure = {}, []

        def close():
            try:
                result['freeze'] = stack.freeze(source, 1)
            except Exception as exc:
                failure.append(exc)
        thread = threading.Thread(target=close, daemon=True)
        thread.start()
        wait_for(lambda: frozen(stack) or failure, 'close_admission never froze the source admission')
        stack.barrier.release('reservation:confirmed', child)
        thread.join(90)
        check(not failure and 'freeze' in result, f'close_admission with a reserved child: {failure}')
        check(stack.smtp.count() == 0, 'the frozen source ran the child')
        record, steps = result['freeze']
        stack.stop_executors(source)
        generation = stack.finish_switch(record, steps)
        stack.barrier.reset()
        stack.executors(target)
        check(await_done(stack, child, f'the {target} executor (child)') == 'completed', 'the child runs on the target')
        await_smtp(stack, 1, f'child on {target}')
        check(recipients(stack) == [TO], f'exactly one message after the switch: {recipients(stack)}')
        if target == 'go':
            exclusive_go(stack, [child])
        from TESTS.handoff.p4_matrix_kit import identity
        identity(stack, {parent: 'source_completed', child: 'target_completed'}, (source, 1), (target, generation),
                 {source: 1, target: 1})
        require_record(TEXT, FAMILY)
    return scenario


# -- smtp_retry ---------------------------------------------------------------------------------------
def smtp_retry_round_trip(stack):
    require_matrix_prerequisites(stack)
    seed(stack)
    stack.smtp.failing = True
    stack.executors('django')
    task = produce_django(stack, MAIL_TASK, kwargs=mail_kwargs({}))
    row = wait_for(lambda: (lambda r: r if r and r['retries'] >= 1 and r['state'] == 'pending' else None)(stack.work_row(task)),
                   'the Django retry of a refused SMTP send was never admitted in place (retries 1, pending)', timeout=90)
    check(row['eta'] is not None and row['eta'] > row['accepted_at'], f'the retry keeps a future eta: {row}')
    eta, accepted = row['eta'], row['accepted_at']
    generation = switch_running(stack, 'django', 1, 'go')
    stack.executors('go')
    for runtime, gen in (('go', generation),):
        moved = stack.work_row(task)
        check((moved['owner_runtime'], moved['owner_generation'], moved['retries'], moved['state']) == (runtime, gen, 1, 'pending'),
              f'after Django -> Go the retry keeps its attempt: {moved}')
        check(moved['eta'] == eta and moved['accepted_at'] == accepted, f'the retry keeps eta {eta} and stamp: {moved}')
    generation = switch_running(stack, 'go', generation, 'django')
    stack.executors('django')
    back = stack.work_row(task)
    check((back['owner_runtime'], back['retries'], back['state'], back['eta']) == ('django', 1, 'pending', eta),
          f'after the round trip the retry keeps attempt and due time: {back}')
    check(stack.smtp.count() == 0 and not [a for a in stack.attempts(task_id=task) if a['outcome'] == 'completed'],
          'the delayed retry ran before its due time')
    require_record(MAIL_TASK, FAMILY)


# -- crash_after_smtp_accept --------------------------------------------------------------------------
def crash_after_smtp_accept(runtime):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(runtime, 1)
        seed(stack)
        task = str(uuid.uuid4())
        stack.barrier.hold('effect:accepted', task)
        processes = stack.executors(runtime)
        if runtime == 'django':
            produce_django(stack, MAIL_TASK, kwargs=mail_kwargs({}), task_id=task)
        else:
            payload = stack.envelope(MAIL_TASK, task, kwargs=mail_kwargs({}), queue=GO_QUEUE, stamp=time.time())
            check(stack.admit('go', task, MAIL_TASK, GO_QUEUE, payload) == 'accepted', 'Go admission')
            stack.push(GO_QUEUE, payload)
        arrival = stack.barrier.wait_arrived('effect:accepted', task, f'the {runtime} executor after SMTP accepted')
        check(stack.smtp.count() == 1, 'SMTP accepted the message')
        for process in processes:
            stack.kill_tree(process, arrival)
        stack.processes[runtime] = []
        stack.barrier.reset()
        stack.executors(runtime)
        row = wait_for(lambda: (lambda r: r if r and r['state'] == 'blocked' else None)(stack.work_row(task)),
                       f'{runtime}: the unconfirmed SMTP effect never blocked the job (contract P3-B: ambiguous_effect)')
        check(row['block_reason'] == 'ambiguous_effect', f'{runtime}: block reason {row["block_reason"]}')
        check([e['state'] for e in stack.effects_rows(task)] == ['ambiguous'], f'{runtime}: effects {stack.effects_rows(task)}')
        check(stack.smtp.count() == 1, f'{runtime}: the restarted executor re-sent an ambiguous message')
        module, store, _ = stack.handoff_parts()
        check(hasattr(module, 'reconcile'), 'contract: job_handoff.reconcile(store, task_id, decision, actor) is missing')
        stack.contract_call('reconcile', module.reconcile, module)(store, task, 'sent',
                                                                   actor='00000000-0000-4000-8000-000000000001')
        check(stack.work_row(task)['state'] == 'completed' and stack.smtp.count() == 1,
              f'{runtime}: reconciled as sent, nothing resent: {stack.work_row(task)["state"]}, {stack.smtp.count()}')
        if runtime == 'go':
            exclusive_go(stack, [task])
        require_record(MAIL_TASK, FAMILY)
    return scenario


SCENARIOS = {}
for _case in CASES.values():
    SCENARIOS.update(standard(_case, FAMILY))
SCENARIOS['send_email_text__after_child_published_forward'] = after_child_published('django', 'go')
SCENARIOS['send_email_text__after_child_published_reverse'] = after_child_published('go', 'django')
SCENARIOS['core_tasks_send_mail_async__smtp_retry_round_trip'] = smtp_retry_round_trip
SCENARIOS['core_tasks_send_mail_async__crash_after_smtp_accept_django'] = crash_after_smtp_accept('django')
SCENARIOS['core_tasks_send_mail_async__crash_after_smtp_accept_go'] = crash_after_smtp_accept('go')
