"""Matrix family workflows: check_workflows_trigger, check_workflows_trigger_cron, workflow_block_execute.

task-coverage.md: trigger a real workflow with a countdown and a follow-up task; switch after scheduling,
before execution and after a child publishes; verify the intended business actions and the actor/tenant
context across both directions. Seed: TESTS/adapters/go/celery_consumer_workflow_process.py (three
associations, owners, members, a minor with tutors, tags, subscriptions and fifteen workflow trees).
The business actions of these tasks ARE their publications (follow-up blocks, send_email_html with the
association as tenant and the owner as reply-to) plus the tutor promotion of an email block; sending the
mail is the email family. Children are read from the ledger (parent_id/root_id lineage, CONTRACT P3-B).

Scenarios (all end with the identity oracle and the task's record):
- check_workflows_trigger__{django,go,queued_forward,queued_reverse}: trigger 'payment_paid' of association C
  (Go producer kwargs as subscription_approve.go publishes them): one follow-up workflow_block_execute
  [W_OK, S1, 1] published and completed on the executing runtime.
- check_workflows_trigger_cron__{...}: cron workflow W6 (tag target): three follow-up blocks [W6, S, 1] for
  S1, S2, S2, completed on the executing runtime.
- workflow_block_execute__{...}: email block of W1 for the minor S2: the first tutor relation is promoted
  (audited), send_email_html (recipients athlete+owner+tutor, sport_association_id A, reply_to owner) and the
  next block [W1, S2, 2] are published with one shared countdown of 1..180 s and stay pending until due.
- workflow_block_execute__after_scheduling_{forward,reverse}: the 2-day wait block of W1 runs on the source
  and publishes [W1, S1, 3] with ETA +172800 s; the switch moves that child to the target, pending, with
  its absolute ETA, parent/root lineage and no source copy (countdown preserved, not executed early).
- check_workflows_trigger_cron__after_child_published_{forward,reverse}: the cron root is held at
  claim:granted, admission closes, the root runs on the source and publishes its three blocks during the
  drain (held), the switch commits and the three blocks run once on the target with the root as parent.
- check_workflows_trigger_cron__eta_scheduled_{forward,reverse}: the cron trigger as automation_workflow_update.go
  schedules it (ETA in 1 h) is accepted on the source and moves to the target pending with that ETA.
Exclusive Go: every child is published to the Go queue (assozeta-go) whatever GO_TASK_QUEUE_TASKS says.
"""
from datetime import datetime, timedelta, timezone
import uuid

from TESTS.handoff.p3_stack import check, wait_for
from TESTS.handoff.p4_family_storage import (Case, decoded, descendants, django_produce, go_produce, run, start)
from TESTS.handoff.p4_matrix_kit import (await_done, exclusive_go, identity, produce_django, produce_go_task,
                                          require_matrix_prerequisites, require_record)

FAMILY = 'workflows'
BLOCK, TRIGGER, CRON = 'workflow_block_execute', 'check_workflows_trigger', 'check_workflows_trigger_cron'


def module():
    from TESTS.adapters.go import celery_consumer_workflow_process as m
    return m


def seed(stack):
    from types import SimpleNamespace
    m = module()
    m.Stack.seed(SimpleNamespace(), None)
    return {'m': m, 'since': datetime.now(timezone.utc)}


def children(stack, roots):
    return [(k, *decoded(stack, k)) for k in descendants(stack, roots)]


def blocks(stack, kids):
    return sorted((str(args[0]), str(args[1]), args[2]) for _, name, args, _, _ in kids if name == BLOCK)


def eta_of(headers):
    return datetime.fromisoformat(headers['eta']) if headers.get('eta') else None


def trigger_verify(stack, ctx, runtime, roots, kids):
    m = ctx['m']
    found = children(stack, roots)
    check(blocks(stack, found) == [(str(m.W_OK), str(m.S1), 1)] and len(found) == 1,
          f'{runtime}: payment_paid publishes exactly the W_OK follow-up block: {[(n, a) for _, n, a, _, _ in found]}')


def cron_verify(stack, ctx, runtime, roots, kids):
    m = ctx['m']
    found = children(stack, roots)
    expected = sorted((str(m.W6), str(s), 1) for s in (m.S1, m.S2, m.S2))
    check(blocks(stack, found) == expected and len(found) == 3,
          f'{runtime}: the tag cron fans out one block per matching tag: {blocks(stack, found)} != {expected}')
    for task_id, _, _, _, headers in found:
        check(headers.get('parent_id') == roots[0] and headers.get('root_id') == roots[0],
              f'{runtime}: block {task_id} keeps the root as parent and root: {headers.get("parent_id")}/{headers.get("root_id")}')


def email_block_verify(stack, ctx, runtime, roots, kids):
    m = ctx['m']
    from application.models.user_models import AssociateTutorRelation
    from auditlog.models import LogEntry
    primary = dict(AssociateTutorRelation.objects.filter(associate_id=m.P2).values_list('id', 'is_primary'))
    check(primary == {1: True, 2: False}, f'{runtime}: the first tutor relation of the minor is promoted: {primary}')
    check(LogEntry.objects.filter(timestamp__gte=ctx['since']).count() == 1, f'{runtime}: the promotion is audited once')
    found = {name: (args, kwargs, headers) for _, name, args, kwargs, headers in children(stack, roots)}
    check(set(found) == {'send_email_html', BLOCK}, f'{runtime}: published {sorted(found)}')
    _, mail, mail_headers = found['send_email_html']
    check(sorted(mail['recipient_list']) == sorted([m.ATHLETE_MAIL, m.OWNER_MAIL, 'tutore@example.test'])
          and str(mail['sport_association_id']) == str(m.A) and mail['reply_to'] == [m.OWNER_MAIL]
          and mail['subject'] == 'Benvenuto', f'{runtime}: mail tenant/actor/recipients: {mail}')
    block_args, _, block_headers = found[BLOCK]
    check([str(a) for a in block_args] == [str(m.W1), str(m.S2), '2'], f'{runtime}: next block {block_args}')
    first, second = eta_of(mail_headers), eta_of(block_headers)
    check(first and second and abs(first - second) < timedelta(seconds=1)
          and timedelta(seconds=1) <= first - ctx['since'] <= timedelta(seconds=200),
          f'{runtime}: one shared 1..180 s countdown for both messages: {first}, {second}')


def pending_eta(stack, ctx, task_id):
    return bool((stack.work_row(task_id) or {}).get('eta'))


TRIGGER_CASE = Case(TRIGGER, seed, {
    'django': lambda s, c: [produce_django(s, TRIGGER, args=['payment_paid', str(c['m'].C), str(c['m'].S1)])],
    'go': lambda s, c: [produce_go_task(s, TRIGGER, kwargs={'trigger_type': 'payment_paid', 'sport_association_id': str(c['m'].C),
                                                            'subscription_id': str(c['m'].S1)})]}, trigger_verify)
CRON_CASE = Case(CRON, seed, {'django': lambda s, c: [produce_django(s, CRON, args=[str(c['m'].W6)])],
                              'go': lambda s, c: [produce_go_task(s, CRON, args=[str(c['m'].W6)])]}, cron_verify)
BLOCK_CASE = Case(BLOCK, seed, {'django': lambda s, c: [produce_django(s, BLOCK, args=[str(c['m'].W1), str(c['m'].S2), 1])],
                                'go': lambda s, c: [produce_go_task(s, BLOCK, args=[str(c['m'].W1), str(c['m'].S2), 1])]},
                  email_block_verify, pending=pending_eta)


def produce(runtime, stack, name, args, eta=None):
    if runtime == 'django':
        return produce_django(stack, name, args=args, eta=eta) if eta else produce_django(stack, name, args=args)
    return produce_go_task(stack, name, args=args, eta=eta)


def after_scheduling(source, target):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        ctx = seed(stack)
        m = ctx['m']
        start(stack, source)
        root = produce(source, stack, BLOCK, [str(m.W1), str(m.S1), 2])
        check(await_done(stack, root, f'the {source} executor') == 'completed', 'the wait block runs on the source')
        child, = wait_for(lambda: descendants(stack, [root]) or None, 'the wait block publishes its follow-up')
        eta = stack.work_row(child)['eta']
        check(eta and timedelta(days=2) - timedelta(seconds=30) <= eta - ctx['since'] <= timedelta(days=2, seconds=30),
              f'the follow-up keeps the 2-day countdown as an absolute ETA: {eta}')
        workers, stack.processes[source] = stack.processes[source], []
        generation = stack.switch(source, 1, target, stop=workers)
        start(stack, target)
        row = stack.work_row(child)
        check(row['eta'] == eta and (row['owner_runtime'], row['owner_generation'], row['state']) == (target, generation, 'pending'),
              f'the scheduled follow-up moves to {target} pending with its ETA: {row["state"]} {row["owner_runtime"]} {row["eta"]}')
        _, args, _, headers = decoded(stack, child)
        check([str(a) for a in args] == [str(m.W1), str(m.S1), '3'] and headers.get('parent_id') == root,
              f'lineage and arguments of the follow-up: {args} {headers.get("parent_id")}')
        if target == 'go':
            exclusive_go(stack, [root, child])
        identity(stack, {root: 'source_completed', child: 'target_pending'}, (source, 1), (target, generation),
                 {source: 1, target: 0})
        require_record(BLOCK, FAMILY)
    return scenario


def after_child_published(source, target):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        ctx = seed(stack)
        m = ctx['m']
        root = str(uuid.uuid4())
        stack.barrier.hold('claim:granted', root)
        start(stack, source)
        if source == 'django':
            produce_django(stack, CRON, args=[str(m.W6)], task_id=root)
        else:
            from TESTS.handoff.p3_stack import GO_QUEUE
            import time
            payload = stack.envelope(CRON, root, args=[str(m.W6)], queue=GO_QUEUE, stamp=time.time())
            check(stack.admit('go', root, CRON, GO_QUEUE, payload) == 'accepted', 'admission')
            stack.push(GO_QUEUE, payload)
        stack.barrier.wait_arrived('claim:granted', root, f'the {source} executor claiming the cron trigger')
        record, steps = stack.freeze(source, 1)
        stack.barrier.release('claim:granted', root)
        check(await_done(stack, root, f'the {source} executor (drain)') == 'completed', 'the root finishes on the source')
        kids = wait_for(lambda: (lambda k: k if len(k) == 3 else None)(descendants(stack, [root])),
                        'the root publishes its three follow-up blocks during the drain')
        check(all(stack.work_row(k)['state'] == 'held' for k in kids), f'children published during the freeze are held: '
              f'{[stack.work_row(k)["state"] for k in kids]}')
        generation = stack.finish_switch(record, steps, stop=list(stack.processes[source]))
        stack.processes[source] = []
        start(stack, target)
        for kid in kids:
            check(await_done(stack, kid, f'the {target} executor') == 'completed', f'block {kid} runs once on {target}')
        cron_verify(stack, ctx, target, [root], kids)
        if target == 'go':
            exclusive_go(stack, kids)
        identity(stack, {root: 'source_completed', **{k: 'target_completed' for k in kids}}, (source, 1), (target, generation),
                 {source: 1, target: 3})
        require_record(CRON, FAMILY)
    return scenario


def eta_scheduled(source, target):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        ctx = seed(stack)
        eta = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
        task = produce(source, stack, CRON, [str(ctx['m'].W6)], eta=eta)
        generation = stack.switch(source, 1, target)
        start(stack, target)
        row = stack.work_row(task)
        check((row['state'], row['owner_runtime'], row['eta']) == ('pending', target, eta),
              f'the scheduled cron trigger keeps its ETA on {target}: {row["state"]} {row["owner_runtime"]} {row["eta"]}')
        check(not descendants(stack, [task]), 'the scheduled trigger did not run early')
        identity(stack, {task: 'target_pending'}, (source, 1), (target, generation), {source: 0, target: 0})
        require_record(CRON, FAMILY)
    return scenario


def standard(case):
    slug = case.task
    return {f'{slug}__{label}': (lambda mode: lambda stack: run(stack, case, FAMILY, mode))(mode)
            for label, mode in (('django', 'django'), ('go', 'go'), ('queued_forward', 'forward'), ('queued_reverse', 'reverse'))}


SCENARIOS = {**standard(TRIGGER_CASE), **standard(CRON_CASE), **standard(BLOCK_CASE)}
for _source, _target, _label in (('django', 'go', 'forward'), ('go', 'django', 'reverse')):
    SCENARIOS[f'{BLOCK}__after_scheduling_{_label}'] = after_scheduling(_source, _target)
    SCENARIOS[f'{CRON}__after_child_published_{_label}'] = after_child_published(_source, _target)
    SCENARIOS[f'{CRON}__eta_scheduled_{_label}'] = eta_scheduled(_source, _target)
