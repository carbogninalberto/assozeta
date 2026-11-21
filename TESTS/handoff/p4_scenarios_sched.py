"""Phase 4 part A scenarios: scheduling authority (contract: TESTS/handoff/test_p4_scheduling.py).

Each scenario runs in the driver on a reset P4 stack and raises ContractFailure on the first
unmet contract point. Time is the scheduler clock seam (stack.tick); every wait polls durable
evidence or a barrier arrival, never a timed sleep.
"""
from datetime import datetime, timedelta, timezone
import os
import signal

from TESTS.handoff import p3_oracle
from TESTS.handoff.p3_stack import DB_TASK, ContractFailure, check, wait_for
from TESTS.handoff.p4_stack import P4Stack, clock_key, day_start, utc

STACK = P4Stack
CUSTOM = 'p4-probe'
H = timedelta(hours=1)
M = timedelta(minutes=1)
S = timedelta(seconds=1)


# -- shared steps -------------------------------------------------------------------------------
def declared_names(stack):
    return set(stack.app.conf.beat_schedule) | {'celery.backend_cleanup'}


def boot(stack, runtime, *, disable=True):
    """Start the runtime's scheduler at the real current instant (seeding stamps are never in the
    future of that clock, so no seeded definition is due), wait for its first pass, then disable
    every seeded definition so only the scenario's own definitions can fire."""
    instant = datetime.now(timezone.utc).replace(microsecond=0)
    stack.set_clock(instant)
    process = stack.beat() if runtime == 'django' else stack.go_scheduler()
    stack.tick(instant, who=f'the {runtime} scheduler (first pass)')
    if disable:
        stack.disable_all()
    return process


def start(stack, runtime):
    return stack.beat() if runtime == 'django' else stack.go_scheduler()


def lease_of(stack, runtime):
    lease = wait_for(lambda: (stack.lease() or {}).get('runtime') == runtime and stack.lease(),
                     f'the {runtime} scheduler holds public.assozeta_scheduler_lease (contract: assozeta_scheduler_acquire)')
    return lease


def receipts(stack, name=CUSTOM):
    return stack.receipts(name)


def dispatched(stack, name=CUSTOM):
    return [r['dispatched_at'].astimezone(timezone.utc) for r in receipts(stack, name)]


def completed_attempts(stack, task_id):
    return [a for a in stack.attempts(task_id=task_id) if a['outcome'] == 'completed']


def await_completion(stack, task_id, who):
    return wait_for(lambda: completed_attempts(stack, task_id), f'{who} never completed the occurrence job {task_id}', timeout=60)


def one_receipt(stack, *, runtime, at, name=CUSTOM, generation=None):
    rows = receipts(stack, name)
    check(len(rows) == 1, f'{name}: expected exactly one occurrence receipt, found {len(rows)}: '
                          f'{[(r["runtime"], r["dispatched_at"].isoformat()) for r in rows]}')
    row = rows[0]
    check(row['runtime'] == runtime, f'{name}: occurrence published by {row["runtime"]}, expected {runtime}')
    check(row['dispatched_at'] == at, f'{name}: dispatched_at {row["dispatched_at"].isoformat()}, expected {at.isoformat()} '
                                      '(the cursor is the dispatch instant of the scheduler clock)')
    check(row['task_id'], f'{name}: the receipt names no task_id (the logical job of the occurrence)')
    if generation is not None:
        check(row['ownership_generation'] == generation, f'{name}: receipt generation {row["ownership_generation"]} != {generation}')
    return row


# -- A0/A1/A2: declarations and seeding -------------------------------------------------------------
CANONICAL_EXAMPLES = {
    'renew-memberships-payments': {'task': 'renew_memberships_payments', 'schedule': {
        'kind': 'crontab', 'minute': '10', 'hour': '0', 'day_of_week': '*', 'day_of_month': '*', 'month_of_year': '*',
        'timezone': 'UTC'}},
    'deliver-whatsapp': {'task': 'communications.tasks.deliver_whatsapp',
                         'schedule': {'kind': 'interval', 'every': 10, 'period': 'seconds'}},
    'notify-backup-failures': {'task': 'instance.backups.notify_failures',
                               'schedule': {'kind': 'interval', 'every': 300, 'period': 'seconds'}},
    'celery.backend_cleanup': {'task': 'celery.backend_cleanup', 'schedule': {
        'kind': 'crontab', 'minute': '0', 'hour': '4', 'day_of_week': '*', 'day_of_month': '*', 'month_of_year': '*',
        'timezone': 'UTC'}},
}
OPTION_KEYS = {'queue', 'exchange', 'routing_key', 'priority', 'headers', 'expire_seconds', 'expires'}


def declarations_match(stack):
    """Django's and Go's embedded periodic declarations are one list in one canonical format."""
    stack.require_schedule_schema()
    try:
        from instance.scheduler import periodic_declarations
    except ImportError:
        raise ContractFailure('contract: instance.scheduler.periodic_declarations() is missing') from None
    django = sorted(periodic_declarations(), key=lambda d: d['name'])
    go = sorted(stack.print_declarations(), key=lambda d: d['name'])
    check({d['name'] for d in django} == declared_names(stack),
          f'periodic_declarations() must list app.conf.beat_schedule plus celery.backend_cleanup: {[d["name"] for d in django]}')
    for entry in django:
        check(set(entry) == {'name', 'task', 'schedule', 'args', 'kwargs', 'options'} and set(entry['options']) == OPTION_KEYS,
              f'canonical declaration keys: {entry}')
    by_name = {d['name']: d for d in django}
    for name, expected in CANONICAL_EXAMPLES.items():
        for key, value in expected.items():
            check(by_name[name][key] == value, f'{name}.{key}: {by_name[name][key]!r}, expected {value!r}')
    check(by_name['deliver-whatsapp']['options']['expires'] == 10, 'deliver-whatsapp keeps options.expires=10')
    check(by_name['celery.backend_cleanup']['options']['expire_seconds'] == 43200, 'backend_cleanup expire_seconds 43200')
    check(go == django, 'cmd/scheduler --print-declarations differs from instance.scheduler.periodic_declarations(): '
                        + str([(d['name'], g) for d, g in zip(django, go) if d != g][:3] or (len(django), len(go))))


def seed_once_fresh_beat(stack):
    """A fresh database: Beat seeds every declared definition once; a restart and a non-effective
    Go scheduler change nothing."""
    stack.require_schedule_schema()
    beat = boot(stack, 'django', disable=False)
    definitions = stack.definitions()
    check(set(definitions) == declared_names(stack), f'seeded names {sorted(definitions)}')
    seeds = stack.seed_rows()
    check(set(seeds) == declared_names(stack) and {s['seeded_by'] for s in seeds.values()} == {'django'},
          f'public.assozeta_periodic_seed records each seeded declaration once (seeded_by django): {sorted(seeds)}')
    stack.stop(beat)
    instant = datetime.now(timezone.utc).replace(microsecond=0) + S
    stack.beat()
    stack.tick(instant, 'Beat after a restart')
    check(stack.definitions() == definitions, 'a Beat restart changed seeded definitions')
    check(stack.seed_rows() == seeds, 'a Beat restart re-seeded (seed records changed)')
    stack.stop(stack.scheduler_processes[-1])
    stack.go_scheduler()
    stack.tick(instant + S, 'the Go scheduler (not effective)')
    check(stack.definitions() == definitions and stack.seed_rows() == seeds,
          'a non-effective Go scheduler changed the seeded definitions')
    check(not stack.receipts(), f'nothing was due, yet occurrences were published: {stack.receipts()}')


def seed_fresh_go_equals_beat(stack):
    """Go on a fresh database where Beat has never run seeds exactly what Beat would seed."""
    stack.require_schedule_schema()
    stack.set_authority('go', 1)
    go_process = boot(stack, 'go', disable=False)
    go_definitions, go_seeds = stack.definitions(), stack.seed_rows()
    check(go_definitions, 'the Go scheduler on a fresh database where Beat never ran created no periodic definitions')
    stack.stop(go_process)
    stack.reset()
    boot(stack, 'django', disable=False)
    django_definitions, django_seeds = stack.definitions(), stack.seed_rows()
    differences = {name: (go_definitions.get(name), django_definitions.get(name))
                   for name in set(go_definitions) | set(django_definitions)
                   if go_definitions.get(name) != django_definitions.get(name)}
    check(not differences, f'Go-seeded definitions differ from Beat-seeded ones (go, django): {differences}')
    strip = lambda rows: {n: (r['task'], r['definition'], r['sha256']) for n, r in rows.items()}  # noqa: E731
    check(strip(go_seeds) == strip(django_seeds), 'seed records (task, definition, definition_sha256) differ between runtimes')
    check({r['seeded_by'] for r in go_seeds.values()} == {'go'}, 'Go seed records say seeded_by go')


def customize(stack):
    models = stack.beat_models()
    pairing = stack.periodic('bakney-pairing-maintenance')
    pairing.interval, _ = models.IntervalSchedule.objects.get_or_create(every=120, period='seconds')
    pairing.save()
    reminders = stack.periodic('send-reminders')
    reminders.enabled = False
    reminders.save()
    exports = stack.periodic('cleanup-old-exports')
    exports.crontab = models.CrontabSchedule.objects.create(minute='45', hour='3', day_of_week='*', day_of_month='*',
                                                            month_of_year='*', timezone='Europe/Rome')
    exports.save()
    audit = stack.periodic('delete-old-audit-logs')
    audit.kwargs = '{"days": 400}'
    audit.save()
    stack.periodic('auto-archive-subscription').delete()
    stack.add_definition('custom-unknown', task='custom.unknown.task', every=300)
    stack.add_definition('custom-one-off', clocked=utc('2026-10-01T08:00:00+00:00'), one_off=True, enabled=False,
                         total_run_count=1, last_run_at=utc('2026-10-01T08:00:00+00:00'))
    stack.add_definition('custom-disabled', every=600, enabled=False)
    stack.changed()


def await_seed_pass(stack, before):
    """Positive evidence that a (re)started Beat finished its startup pass: the Phase 4 tick barrier,
    or -- today -- its overwrite of a customized definition."""
    instant = datetime.now(timezone.utc).replace(microsecond=0) + S
    key = clock_key(instant)
    stack.set_clock(instant)
    wait_for(lambda: stack.barrier.arrival('schedule:ticked', key) or stack.definitions() != before,
             'the restarted Beat finished no evaluation pass (contract: barrier schedule:ticked)', timeout=60)
    return instant


def customized_definitions_survive_restart_and_both_switches(stack):
    """Customized, disabled, deleted, one-off and custom definitions survive a restart (upgrade
    boundary), Django -> Go and Go -> Django."""
    stack.require_handoff_ledger()
    instant = datetime.now(timezone.utc).replace(microsecond=0)
    stack.set_clock(instant)
    beat = stack.beat()
    wait_for(lambda: len(stack.definitions()) >= len(declared_names(stack)), 'Beat seeds the declared definitions')
    customize(stack)
    expected = stack.definitions()
    stack.stop(beat)
    stack.beat()
    await_seed_pass(stack, expected)
    observed = stack.definitions()
    changed = {n: (expected.get(n), observed.get(n)) for n in set(expected) | set(observed) if expected.get(n) != observed.get(n)}
    check(not changed, f'a Beat restart overwrote customized definitions (expected, observed): {changed}')
    stack.require_schedule_schema()
    generation = stack.switch('django', 1, 'go', stop=[stack.scheduler_processes[-1]])
    go_process = stack.go_scheduler()
    stack.tick(datetime.now(timezone.utc).replace(microsecond=0) + 2 * S, 'the Go scheduler after the switch')
    check(stack.definitions() == expected, 'Django -> Go changed customized definitions: '
          + str({n: v for n, v in stack.definitions().items() if expected.get(n) != v}))
    stack.switch('go', generation, 'django', stop=[go_process])
    stack.beat()
    stack.tick(datetime.now(timezone.utc).replace(microsecond=0) + 3 * S, 'Beat after switching back')
    check(stack.definitions() == expected, 'Go -> Django changed customized definitions: '
          + str({n: v for n, v in stack.definitions().items() if expected.get(n) != v}))


def seed_release_update_respects_customization(stack):
    """The shared seed function: a changed declaration updates an uncustomized definition, keeps a
    customized one and never re-creates one the owner deleted."""
    stack.require_schedule_schema()
    import json

    def declaration(name, minute):
        return {'name': name, 'task': DB_TASK, 'args': [], 'kwargs': {},
                'schedule': {'kind': 'crontab', 'minute': minute, 'hour': '1', 'day_of_week': '*', 'day_of_month': '*',
                             'month_of_year': '*', 'timezone': 'UTC'},
                'options': {key: ({} if key == 'headers' else None) for key in OPTION_KEYS}}

    def seed(runtime, minutes):
        rows = stack.sql('SELECT name, outcome FROM public.assozeta_seed_periodic_definitions(%s, %s::jsonb)',
                         (runtime, json.dumps([declaration(n, m) for n, m in minutes.items()])))
        return dict(rows)

    first = seed('django', {'p4-plain': '0', 'p4-custom': '0', 'p4-deleted': '0'})
    check(first == {'p4-plain': 'created', 'p4-custom': 'created', 'p4-deleted': 'created'}, f'first seed: {first}')
    custom = stack.periodic('p4-custom')
    custom.crontab = stack.beat_models().CrontabSchedule.objects.create(minute='5', hour='1', timezone='UTC')
    custom.save()
    stack.periodic('p4-deleted').delete()
    again = seed('go', {'p4-plain': '0', 'p4-custom': '0', 'p4-deleted': '0'})
    check(again == {'p4-plain': 'kept', 'p4-custom': 'kept', 'p4-deleted': 'kept_deleted'}, f'identical re-seed: {again}')
    release = seed('django', {'p4-plain': '30', 'p4-custom': '30', 'p4-deleted': '30', 'p4-new': '30'})
    check(release == {'p4-plain': 'updated', 'p4-custom': 'kept_custom', 'p4-deleted': 'kept_deleted', 'p4-new': 'created'},
          f'changed declarations: {release}')
    definitions = stack.definitions()
    check(definitions['p4-plain']['schedule'][1] == '30', 'the uncustomized definition follows the new declaration')
    check(definitions['p4-custom']['schedule'][1] == '5', 'the customized definition is kept')
    check('p4-deleted' not in definitions, 'a definition the owner deleted is never re-created')


# -- A4: publication is transactional with the cursor and the receipt -----------------------------------
def beat_receipt_cursor_and_crash_before_push(stack):
    """Beat persists receipt + admitted job + cursor in one transaction before the broker push; a
    crash between commit and push publishes that job once on restart and never a second occurrence."""
    stack.require_schedule_schema()
    stack.seed_balance()
    boot(stack, 'django')
    due = day_start(2) + 12 * H
    definition = stack.add_definition(CUSTOM, cron={'minute': '*'}, last_run_at=due - M)
    stack.tick(due - 30 * S, 'Beat (definition reload)')
    stack.barrier.hold('schedule:committed', str(definition.id))
    stack.set_clock(due)
    arrival = stack.barrier.wait_arrived('schedule:committed', str(definition.id), 'Beat publishing a due occurrence')
    row = one_receipt(stack, runtime='django', at=due, generation=1)
    work = stack.work_row(row['task_id'])
    check(work and work['state'] == 'pending' and (work['owner_runtime'], work['owner_generation']) == ('django', 1),
          f'the occurrence job is admitted in the same transaction: {work}')
    last, count, _ = stack.cursor(CUSTOM)
    check((last, count) == (due, 1), f'the cursor (last_run_at, total_run_count) is persisted with the receipt: {(last, count)}')
    check(row['scheduler_generation'] == lease_of(stack, 'django')['scheduler_generation'],
          'the receipt records the publishing scheduler generation')
    check(not stack.redis_copies(row['task_id']), 'barrier schedule:committed is after the commit and before the broker push')
    stack.kill_tree(stack.scheduler_processes[-1], arrival)
    stack.barrier.reset()
    stack.beat()
    stack.tick(due + 5 * S, 'Beat after a crash between commit and push')
    copies = wait_for(lambda: stack.redis_copies(row['task_id']), 'the admitted occurrence job is published after the crash')
    check(sum(len(v) for v in copies.values()) == 1, f'exactly one broker copy: {copies}')
    check(len(receipts(stack)) == 1, f'the restarted Beat published the occurrence again: {receipts(stack)}')
    stack.django_worker()
    await_completion(stack, row['task_id'], 'the Django worker')
    p3_oracle.check(stack, accepted={row['task_id']: 'source_completed'}, source=('django', 1), target=('go', 2),
                    effects={'django': 1, 'go': 0})


# -- A5/A6: the same occurrence on either side of the freeze boundary ------------------------------------
def held_at_due_then_frozen(stack, source, target):
    stack.require_schedule_schema()
    stack.seed_balance()
    stack.set_authority(source, 1)
    boot(stack, source)
    due = day_start(2) + 12 * H
    definition = stack.add_definition(CUSTOM, cron={'minute': '*'}, last_run_at=due - M)
    stack.tick(due - 30 * S, f'the {source} scheduler (definition reload)')
    key = f'{lease_of(stack, source)["scheduler_generation"]}-{definition.id}'
    stack.barrier.hold('schedule:due', key)
    stack.set_clock(due)
    stack.barrier.wait_arrived('schedule:due', key, f'the {source} scheduler at a due occurrence')
    record, steps = stack.freeze(source, 1)
    stack.barrier.release('schedule:due', key)
    stack.barrier.wait_arrived('schedule:ticked', clock_key(due), f'the frozen {source} scheduler')
    check(not receipts(stack), f'the {source} scheduler published while frozen: {receipts(stack)}')
    generation = stack.finish_switch(record, steps, stop=[stack.scheduler_processes[-1]])
    if target == 'go':
        stack.go_worker()
    else:
        stack.django_worker()
    start(stack, target)
    stack.tick(due + 10 * S, f'the {target} scheduler after activation')
    row = one_receipt(stack, runtime=target, at=due + 10 * S, generation=generation)
    await_completion(stack, row['task_id'], f'the {target} executor')
    p3_oracle.check(stack, accepted={row['task_id']: 'source_completed'}, source=(target, generation),
                    target=(source, generation + 1), effects={source: 0, target: 1})


def freeze_before_publication_forward(stack):
    held_at_due_then_frozen(stack, 'django', 'go')


def freeze_before_publication_reverse(stack):
    held_at_due_then_frozen(stack, 'go', 'django')


def committed_then_frozen(stack, source, target):
    stack.require_schedule_schema()
    stack.seed_balance()
    stack.set_authority(source, 1)
    boot(stack, source)
    due = day_start(2) + 12 * H
    definition = stack.add_definition(CUSTOM, cron={'minute': '*'}, last_run_at=due - M)
    stack.tick(due - 30 * S, f'the {source} scheduler (definition reload)')
    stack.barrier.hold('schedule:committed', str(definition.id))
    stack.set_clock(due)
    stack.barrier.wait_arrived('schedule:committed', str(definition.id), f'the {source} scheduler committing an occurrence')
    record, steps = stack.freeze(source, 1)
    stack.barrier.release('schedule:committed', str(definition.id))
    stack.barrier.wait_arrived('schedule:ticked', clock_key(due), f'the frozen {source} scheduler')
    row = one_receipt(stack, runtime=source, at=due, generation=1)
    generation = stack.finish_switch(record, steps, stop=[stack.scheduler_processes[-1]])
    if target == 'go':
        stack.go_worker()
    else:
        stack.django_worker()
    start(stack, target)
    stack.tick(due + 10 * S, f'the {target} scheduler after activation')
    stack.tick(due + M, f'the {target} scheduler at the next slot')
    rows = receipts(stack)
    check([r['runtime'] for r in rows] == [source, target] and rows[1]['dispatched_at'] == due + M,
          f'the frozen occurrence is not published again by {target}; its next slot is: '
          f'{[(r["runtime"], r["dispatched_at"].isoformat()) for r in rows]}')
    await_completion(stack, row['task_id'], f'the {target} executor (transferred occurrence)')
    await_completion(stack, rows[1]['task_id'], f'the {target} executor (next occurrence)')
    p3_oracle.check(stack, accepted={row['task_id']: 'target_completed', rows[1]['task_id']: 'target_completed'},
                    source=(source, 1), target=(target, generation), effects={source: 0, target: 2})


def freeze_after_commit_forward(stack):
    committed_then_frozen(stack, 'django', 'go')


def freeze_after_commit_reverse(stack):
    committed_then_frozen(stack, 'go', 'django')


# -- A7/A8/A9: fresh Go, switching back, missed ticks ------------------------------------------------------
def fresh_go_where_beat_never_ran(stack):
    stack.require_schedule_schema()
    stack.seed_balance()
    stack.set_authority('go', 1)
    boot(stack, 'go')
    name = 'reset-communication-daily-email-balance'
    midnight = day_start(1)
    row = stack.periodic(name)
    row.enabled, row.last_run_at = True, midnight - timedelta(days=1)
    row.save()
    stack.go_worker()
    stack.tick(midnight - 30 * S, 'the Go scheduler before midnight')
    check(not receipts(stack, name), 'the daily reset fired before its slot')
    stack.tick(midnight, 'the Go scheduler at midnight')
    receipt = one_receipt(stack, runtime='go', at=midnight, name=name, generation=1)
    await_completion(stack, receipt['task_id'], 'the Go worker')
    p3_oracle.check(stack, accepted={receipt['task_id']: 'source_completed'}, source=('go', 1), target=('django', 2),
                    effects={'django': 0, 'go': 1})


def switch_back_before_next_due(stack):
    stack.require_schedule_schema()
    stack.seed_balance()
    boot(stack, 'django')
    due = day_start(2) + 12 * H
    stack.add_definition(CUSTOM, cron={'minute': '0', 'hour': '12'}, last_run_at=due - timedelta(days=1))
    stack.tick(due - 4 * H, 'Beat')
    generation = stack.switch('django', 1, 'go', stop=[stack.scheduler_processes[-1]])
    stack.go_scheduler()
    stack.tick(due - 3 * H, 'the Go scheduler')
    generation = stack.switch('go', generation, 'django', stop=[stack.scheduler_processes[-1]])
    stack.beat()
    stack.django_worker()
    stack.tick(due - 2 * H, 'Beat after switching back')
    check(not receipts(stack), f'nothing was due before {due.isoformat()}: {receipts(stack)}')
    stack.tick(due, 'Beat at the due time')
    row = one_receipt(stack, runtime='django', at=due, generation=generation)
    check(lease_of(stack, 'django')['ownership_generation'] == generation, 'the lease is bound to the current generation')
    await_completion(stack, row['task_id'], 'the Django worker')
    p3_oracle.check(stack, accepted={row['task_id']: 'source_completed'}, source=('django', generation),
                    target=('go', generation + 1), effects={'django': 1, 'go': 0})


def missed_ticks_once_on_resume(stack):
    """A frozen source publishes nothing; the target applies Beat's missed-tick policy once (one
    catch-up occurrence at resume, cursor = dispatch instant, no replay of every missed slot)."""
    stack.require_schedule_schema()
    stack.seed_balance()
    boot(stack, 'django')
    worker = stack.django_worker()
    start_at = day_start(2) + 12 * H
    stack.add_definition(CUSTOM, cron={'minute': '*'}, last_run_at=start_at - M)
    stack.tick(start_at, 'Beat at the first slot')
    first = one_receipt(stack, runtime='django', at=start_at, generation=1)
    await_completion(stack, first['task_id'], 'the Django worker')
    stack.tick(start_at + 30 * S, 'Beat')
    record, steps = stack.freeze('django', 1)
    for minute in range(1, 6):
        stack.tick(start_at + minute * M, 'the frozen Beat')
    check(len(receipts(stack)) == 1, f'the frozen source published missed slots: {dispatched(stack)}')
    generation = stack.finish_switch(record, steps, stop=[stack.scheduler_processes[-1], worker])
    stack.go_worker()
    stack.go_scheduler()
    for instant in (start_at + 5 * M + 10 * S, start_at + 5 * M + 40 * S, start_at + 6 * M):
        stack.tick(instant, 'the Go scheduler after activation')
    expected = [start_at, start_at + 5 * M + 10 * S, start_at + 6 * M]
    check(dispatched(stack) == expected, f'occurrences {[d.isoformat() for d in dispatched(stack)]}, expected '
                                         f'{[d.isoformat() for d in expected]} (one catch-up at resume)')
    later = receipts(stack)[1:]
    for row in later:
        await_completion(stack, row['task_id'], 'the Go worker')
    p3_oracle.check(stack, accepted={first['task_id']: 'source_completed', **{r['task_id']: 'target_completed' for r in later}},
                    source=('django', 1), target=('go', generation), effects={'django': 1, 'go': 2})


# -- A10/A11/A12/A13: definition types against the uninterrupted Beat control -------------------------------
def walk(stack, runtime, definitions, instants, switch_after=None):
    """Reset, boot `runtime`, create `definitions` {name: add_definition kwargs}, tick every instant
    (switching to the other runtime after index `switch_after`); returns {name: [dispatched_at]}."""
    stack.reset()
    stack.set_authority(runtime, 1)
    boot(stack, runtime)
    for name, fields in definitions.items():
        stack.add_definition(name, **fields)
    generation, current = 1, runtime
    for index, instant in enumerate(instants):
        stack.tick(instant, f'the {current} scheduler')
        if index == switch_after:
            target = 'go' if current == 'django' else 'django'
            generation = stack.switch(current, generation, target, stop=[stack.scheduler_processes[-1]])
            start(stack, target)
            current = target
    return {name: dispatched(stack, name) for name in definitions}


def compare_walks(label, control, observed):
    differences = {name: ([d.isoformat() for d in control[name]], [d.isoformat() for d in observed[name]])
                   for name in control if control[name] != observed[name]}
    check(not differences, f'{label} differs from the uninterrupted Beat control (control, observed): {differences}')


ROME = {'minute': '30', 'hour': '2', 'timezone': 'Europe/Rome'}
SPRING = [utc(t) for t in ('2026-03-29T00:30:00+00:00', '2026-03-29T00:59:00+00:00', '2026-03-29T01:00:00+00:00',
                           '2026-03-29T01:15:00+00:00', '2026-03-29T01:29:00+00:00', '2026-03-29T01:30:00+00:00',
                           '2026-03-29T01:31:00+00:00', '2026-03-29T02:30:00+00:00', '2026-03-30T00:29:00+00:00',
                           '2026-03-30T00:30:00+00:00', '2026-03-30T00:31:00+00:00')]
FALL = [utc(t) for t in ('2026-10-25T00:15:00+00:00', '2026-10-25T00:30:00+00:00', '2026-10-25T00:45:00+00:00',
                         '2026-10-25T01:00:00+00:00', '2026-10-25T01:15:00+00:00', '2026-10-25T01:30:00+00:00',
                         '2026-10-25T01:45:00+00:00', '2026-10-26T01:29:00+00:00', '2026-10-26T01:30:00+00:00')]


def dst_forward_and_repeated_local_times(stack):
    stack.require_schedule_schema()
    spring = {CUSTOM: dict(cron=ROME, last_run_at=utc('2026-03-28T01:30:00+00:00'))}
    fall = {CUSTOM: dict(cron=ROME, last_run_at=utc('2026-10-24T00:30:00+00:00'))}
    control_spring = walk(stack, 'django', spring, SPRING)
    check(control_spring[CUSTOM] == [utc('2026-03-29T01:30:00+00:00'), utc('2026-03-30T00:30:00+00:00')],
          f'Beat control (spring gap) moved: {control_spring}')
    compare_walks('Go across the spring gap', control_spring, walk(stack, 'go', spring, SPRING))
    control_fall = walk(stack, 'django', fall, FALL)
    check(control_fall[CUSTOM] == [utc('2026-10-25T00:30:00+00:00'), utc('2026-10-26T01:30:00+00:00')],
          f'Beat control (fall fold) moved: {control_fall}')
    compare_walks('Go across the fall fold', control_fall, walk(stack, 'go', fall, FALL))
    compare_walks('Django -> Go between the two folds', control_fall, walk(stack, 'django', fall, FALL, switch_after=2))
    compare_walks('Go -> Django between the two folds', control_fall, walk(stack, 'go', fall, FALL, switch_after=2))


def interval_and_cron_types(stack):
    stack.require_schedule_schema()
    base = utc('2026-11-02T08:00:00+00:00')  # a Monday
    definitions = {
        'p4-interval': dict(every=600, last_run_at=base - 9 * M),
        'p4-quarter': dict(cron={'minute': '*/15'}, last_run_at=base - 16 * M),
        'p4-monday': dict(cron={'minute': '0', 'hour': '9', 'day_of_week': '1'}, last_run_at=base - timedelta(days=7)),
        'p4-archive': dict(cron={'minute': '0', 'hour': '8', 'day_of_month': '1-4', 'month_of_year': '1,6,9,11'},
                           last_run_at=base - timedelta(days=1)),
    }
    instants = [base + k * 5 * M + 7 * S for k in range(0, 15)] + [base + H, base + H + 30 * S]
    instants = sorted(set(instants))
    control = walk(stack, 'django', definitions, instants)
    check(all(control.values()), f'every probe definition fires at least once in the Beat control: {control}')
    compare_walks('Go (interval, */15, day_of_week, day_of_month+month)', control, walk(stack, 'go', definitions, instants))
    compare_walks('Django -> Go mid-walk', control, walk(stack, 'django', definitions, instants, switch_after=6))
    compare_walks('Go -> Django mid-walk', control, walk(stack, 'go', definitions, instants, switch_after=6))


def edited_definition_during_transition(stack):
    """D08: a definition edited while admission is frozen takes effect on the target; the old
    definition never fires after the edit."""
    stack.require_schedule_schema()
    boot(stack, 'django')
    due = day_start(2) + 12 * H
    definition = stack.add_definition(CUSTOM, cron={'minute': '0', 'hour': '12'}, last_run_at=due - timedelta(days=1))
    stack.tick(due - 2 * H, 'Beat')
    record, steps = stack.freeze('django', 1)
    definition.crontab = stack.beat_models().CrontabSchedule.objects.create(minute='30', hour='11', timezone='UTC')
    definition.save()
    stack.tick(due - 30 * M, 'the frozen Beat (edited definition due)')
    check(not receipts(stack), 'a frozen scheduler published the edited definition')
    stack.finish_switch(record, steps, stop=[stack.scheduler_processes[-1]])
    stack.go_scheduler()
    for instant in (due - 30 * M + 5 * S, due, due + 5 * S):
        stack.tick(instant, 'the Go scheduler')
    check(dispatched(stack) == [due - 30 * M + 5 * S],
          f'expected one occurrence of the edited definition at resume and none of the old 12:00 slot: '
          f'{[d.isoformat() for d in dispatched(stack)]}')


def disabled_and_one_off_definitions(stack):
    """A disabled definition never fires; a one-off fires once overall, in both directions."""
    stack.require_schedule_schema()
    for source in ('django', 'go'):
        target = 'go' if source == 'django' else 'django'
        stack.reset()
        stack.set_authority(source, 1)
        boot(stack, source)
        due = day_start(2) + 12 * H
        stack.add_definition('p4-disabled', cron={'minute': '*'}, enabled=False, last_run_at=due - M)
        stack.add_definition('p4-one-off', clocked=due, one_off=True, last_run_at=due - H)
        stack.tick(due, f'the {source} scheduler at the one-off instant')
        stack.tick(due + M, f'the {source} scheduler')
        check(len(dispatched(stack, 'p4-one-off')) == 1 and dispatched(stack, 'p4-one-off')[0] == due,
              f'{source}: the one-off fires once at its clocked time: {dispatched(stack, "p4-one-off")}')
        generation = stack.switch(source, 1, target, stop=[stack.scheduler_processes[-1]])
        start(stack, target)
        for minutes in (2, 3, 60):
            stack.tick(due + minutes * M, f'the {target} scheduler')
        check(len(dispatched(stack, 'p4-one-off')) == 1, f'{source} -> {target}: the one-off fired again: '
                                                         f'{dispatched(stack, "p4-one-off")}')
        check(not dispatched(stack, 'p4-disabled'), f'{source} -> {target}: a disabled definition fired')
        check(not stack.periodic('p4-one-off').enabled, f'{source} -> {target}: the completed one-off stays disabled')
        check(generation == 2, 'generation')


# -- A14: a lost scheduler heartbeat cannot admit a second scheduler generation ------------------------------
def lost_heartbeat(stack, runtime):
    stack.require_schedule_schema()
    stack.seed_balance()
    stack.set_authority(runtime, 1)
    boot(stack, runtime)
    due = day_start(2) + 12 * H
    definition = stack.add_definition(CUSTOM, cron={'minute': '*'}, last_run_at=due - M)
    stack.tick(due - 30 * S, f'scheduler A ({runtime})')
    lease = lease_of(stack, runtime)
    generation_a, holder_a = lease['scheduler_generation'], lease['holder']
    key = f'{generation_a}-{definition.id}'
    stack.barrier.hold('schedule:due', key)
    stack.set_clock(due)
    arrival = stack.barrier.wait_arrived('schedule:due', key, f'scheduler A ({runtime}) at the due occurrence')
    os.kill(int(arrival['pid']), signal.SIGSTOP)  # A loses its heartbeat mid-decision
    try:
        start(stack, runtime)
        lease_b = wait_for(lambda: (lambda l: l if l and l['scheduler_generation'] > generation_a else None)(stack.lease()),
                           'a second scheduler never acquired the expired lease (contract: assozeta_scheduler_acquire)')
        check(lease_b['holder'] != holder_a, 'the new generation belongs to the new scheduler')
        wait_for(lambda: receipts(stack), f'scheduler B ({runtime}) never published the due occurrence')
    finally:
        os.kill(int(arrival['pid']), signal.SIGCONT)
    stack.barrier.release('schedule:due', key)
    stack.barrier.wait_arrived('schedule:fenced', key, f'scheduler A ({runtime}) resuming with a stale generation '
                                                       '(contract: barrier schedule:fenced when assozeta_scheduler_fence refuses)')
    rows = receipts(stack)
    check(len(rows) == 1 and rows[0]['scheduler_generation'] == lease_b['scheduler_generation'],
          f'only the current scheduler generation publishes: {[(r["scheduler_generation"], r["runtime"]) for r in rows]}')
    beats = lease_b['heartbeat_at']
    wait_for(lambda: stack.lease()['heartbeat_at'] > beats, 'scheduler B keeps renewing its lease')
    check(stack.lease()['scheduler_generation'] == lease_b['scheduler_generation'],
          'the resumed scheduler A admitted another scheduler generation while B was alive')
    stack.tick(due + M, f'scheduler B ({runtime}) at the next slot')
    rows = receipts(stack)
    check(len(rows) == 2 and {r['scheduler_generation'] for r in rows} == {lease_b['scheduler_generation']},
          f'next slot: {[(r["scheduler_generation"], r["dispatched_at"].isoformat()) for r in rows]}')


def lost_heartbeat_go(stack):
    lost_heartbeat(stack, 'go')


def lost_heartbeat_django(stack):
    lost_heartbeat(stack, 'django')


# -- A15/A16: Go equivalents of the two scheduled exceptions ------------------------------------------------
def backup_notifications_counted_once_across_crash(stack):
    """Go mode: notify-backup-failures is published by the Go scheduler as one broker occurrence for
    the Go consumer; a crash between the occurrence commit and the broker push publishes it once."""
    stack.require_schedule_schema()
    stack.set_authority('go', 1)
    boot(stack, 'go')
    name = 'notify-backup-failures'
    due = day_start(2) + 12 * H
    row = stack.periodic(name)
    row.enabled, row.last_run_at = True, due - 301 * S
    row.save()
    stack.tick(due - 2 * S, 'the Go scheduler')
    check(not receipts(stack, name), 'notify-backup-failures fired early')
    stack.barrier.hold('schedule:committed', str(row.id))
    stack.set_clock(due)
    arrival = stack.barrier.wait_arrived('schedule:committed', str(row.id), 'the Go scheduler committing the backup-notice occurrence')
    receipt = one_receipt(stack, runtime='go', at=due, name=name, generation=1)
    stack.kill_tree(stack.scheduler_processes[-1], arrival)
    stack.barrier.reset()
    stack.go_scheduler()
    stack.tick(due + 10 * S, 'the restarted Go scheduler')
    copies = wait_for(lambda: stack.redis_copies(receipt['task_id']), 'the committed backup-notice occurrence was never published')
    check(list(copies) == ['assozeta-go'] and len(copies['assozeta-go']) == 1,
          f'one broker copy on the Go consumer queue: {sorted((k, len(v)) for k, v in copies.items())}')
    check(len(receipts(stack, name)) == 1, 'the occurrence was counted twice across the crash')
    work = stack.work_row(receipt['task_id'])
    check(work and work['task_name'] == 'instance.backups.notify_failures' and work['transport'] == 'broker'
          and (work['owner_runtime'], work['owner_generation']) == ('go', 1), f'ledger row: {work}')


def backend_cleanup_on_go(stack):
    """celery.backend_cleanup: equivalent result retention (1 day) in Go mode on a fresh install and
    on an install upgraded from Beat, once per occurrence across the switch boundary."""
    stack.require_schedule_schema()
    now = datetime.now(timezone.utc)
    name = 'celery.backend_cleanup'

    def expired(*names):
        return {n: now - timedelta(days=2) for n in names}

    def arm(due):
        row = stack.periodic(name)
        row.enabled, row.last_run_at = True, due - timedelta(days=1)
        row.save()

    # fresh Go install
    stack.set_authority('go', 1)
    boot(stack, 'go')
    stack.seed_results({**expired('p4-expired-1', 'p4-expired-2'), 'p4-recent': now - H})
    day = day_start(2) + 4 * H
    arm(day)
    stack.go_worker()
    stack.go_consumer()
    stack.tick(day, 'the Go scheduler at 04:00')
    one_receipt(stack, runtime='go', at=day, name=name, generation=1)
    wait_for(lambda: stack.result_ids() == {'p4-recent'} or not {'p4-expired-1', 'p4-expired-2'} & stack.result_ids(),
             'Go mode never removed the expired results (contract: Go result retention)', timeout=60)
    check({'p4-recent'} <= stack.result_ids(), 'an unexpired result was removed')
    # upgraded install: Beat seeded and ran the cleanup, then Django -> Go
    stack.reset()
    boot(stack, 'django')
    stack.seed_results({**expired('p4-expired-3'), 'p4-recent': now - H})
    arm(day)
    stack.django_worker()
    stack.tick(day, 'Beat at 04:00')
    wait_for(lambda: 'p4-expired-3' not in stack.result_ids(), 'the Django control cleanup')
    stack.seed_results(expired('p4-expired-4'))
    generation = stack.switch('django', 1, 'go', stop=[stack.scheduler_processes[-1]])
    stack.go_worker()
    stack.go_consumer()
    stack.go_scheduler()
    stack.tick(day + timedelta(hours=12), 'the Go scheduler, not due')
    check('p4-expired-4' in stack.result_ids(), 'cleanup ran outside its occurrence')
    stack.tick(day + timedelta(days=1), 'the Go scheduler at the next 04:00')
    wait_for(lambda: 'p4-expired-4' not in stack.result_ids(), 'Go mode never removed the expired result after the switch',
             timeout=60)
    check('p4-recent' in stack.result_ids(), 'an unexpired result was removed')
    rows = receipts(stack, name)
    check([(r['runtime'], r['ownership_generation']) for r in rows] == [('django', 1), ('go', generation)],
          f'one cleanup occurrence per day across the switch: {[(r["runtime"], r["dispatched_at"].isoformat()) for r in rows]}')


SCENARIOS = {
    'declarations_match': declarations_match,
    'seed_once_fresh_beat': seed_once_fresh_beat,
    'seed_fresh_go_equals_beat': seed_fresh_go_equals_beat,
    'customized_definitions_survive_restart_and_both_switches': customized_definitions_survive_restart_and_both_switches,
    'seed_release_update_respects_customization': seed_release_update_respects_customization,
    'beat_receipt_cursor_and_crash_before_push': beat_receipt_cursor_and_crash_before_push,
    'freeze_before_publication_forward': freeze_before_publication_forward,
    'freeze_before_publication_reverse': freeze_before_publication_reverse,
    'freeze_after_commit_forward': freeze_after_commit_forward,
    'freeze_after_commit_reverse': freeze_after_commit_reverse,
    'fresh_go_where_beat_never_ran': fresh_go_where_beat_never_ran,
    'switch_back_before_next_due': switch_back_before_next_due,
    'missed_ticks_once_on_resume': missed_ticks_once_on_resume,
    'dst_forward_and_repeated_local_times': dst_forward_and_repeated_local_times,
    'interval_and_cron_types': interval_and_cron_types,
    'edited_definition_during_transition': edited_definition_during_transition,
    'disabled_and_one_off_definitions': disabled_and_one_off_definitions,
    'lost_heartbeat_go': lost_heartbeat_go,
    'lost_heartbeat_django': lost_heartbeat_django,
    'backup_notifications_counted_once_across_crash': backup_notifications_counted_once_across_crash,
    'backend_cleanup_on_go': backend_cleanup_on_go,
}
