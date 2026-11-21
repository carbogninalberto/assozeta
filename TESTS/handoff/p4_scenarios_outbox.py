"""Phase 4 part B scenarios: WhatsApp outbox ownership and the realtime relay (D16).

Contract: TESTS/handoff/test_p4_outbox_realtime.py. Provider-side effects are counted by the
WhatsApp stub (one record per accepted send, keyed by the delivery id the request carries);
realtime deliveries by a pub/sub subscriber on the cross-runtime mirror channel.
"""
from datetime import datetime, timedelta, timezone
import threading
import time
import uuid

from TESTS.handoff.p3_stack import ContractFailure, check, wait_for
from TESTS.handoff.p3_scenarios_db import consumer_of, heartbeats_after
from TESTS.handoff.p4_outbox_stack import P4OutboxStack

STACK = P4OutboxStack
POLL = 'communications.tasks.deliver_whatsapp'


def poll_django(stack, expires=60):
    """Beat's production publication of the poll (Task.apply_async, admitted like every task)."""
    from communications.tasks import deliver_whatsapp
    deliver_whatsapp.apply_async(expires=expires)


def status(stack, identifier):
    return stack.delivery(identifier)['status']


def await_sent(stack, ids, who):
    wait_for(lambda: all(status(stack, i) == 'sent' for i in ids), f'{who} never sent {ids}', timeout=60)


def snapshot(stack, ids):
    return {i: stack.delivery(i) for i in ids}


def confirmed_by(stack, identifier, runtime):
    effects = stack.outbox_effects(identifier)
    check(len(effects) == 1, f'delivery {identifier}: expected one outbox effect row, found {effects}')
    effect = effects[0]
    check((effect['outbox'], effect['runtime'], effect['state']) == ('whatsapp', runtime, 'confirmed'),
          f'delivery {identifier}: effect {effect["outbox"]}/{effect["runtime"]}/{effect["state"]}, expected whatsapp/{runtime}/confirmed')
    check(effect['provider_ref'] == stack.whatsapp.message_id(identifier) == stack.delivery(identifier)['provider_message_id'],
          f'delivery {identifier}: provider identity differs between stub, effect and row')


def outbox_consumer(stack, runtime):
    rows = consumer_of(stack, runtime)
    return rows[0]['consumer_id']


def guarded_switch(stack, source, generation, target, **options):
    module = stack.job_handoff()
    blocked = getattr(module, 'TransferBlocked', None)
    try:
        return stack.switch(source, generation, target, **options)
    except Exception as exc:
        if blocked and isinstance(exc, blocked):
            raise ContractFailure(f'{source} -> {target} blocked: {getattr(exc, "blockers", exc)}') from None
        raise


# -- WhatsApp -----------------------------------------------------------------------------------
def whatsapp_single_owner_per_mode(stack):
    """Both deliverers run; only the effective runtime's sends, before and after a switch."""
    stack.require_outbox_schema()
    first = stack.queue_whatsapp(3)
    stack.go_api()
    worker = stack.django_worker()
    stack.django_ready()
    go_outbox = outbox_consumer(stack, 'go')
    poll_django(stack)
    await_sent(stack, first, 'the Django worker (deliver_whatsapp)')
    for identifier in first:
        check(stack.whatsapp.count(identifier) == 1, f'{identifier} was sent {stack.whatsapp.count(identifier)} times')
        confirmed_by(stack, identifier, 'django')
    pending = stack.queue_whatsapp(2)
    heartbeats_after(stack, go_outbox, count=5)
    check(all(status(stack, i) == 'queued' and stack.whatsapp.count(i) == 0 for i in pending),
          'the Go embedded poller delivered while Django owns the outbox')
    guarded_switch(stack, 'django', 1, 'go', stop=[worker])
    await_sent(stack, pending, 'the Go embedded poller after activation')
    later = stack.queue_whatsapp(2)
    await_sent(stack, later, 'the Go embedded poller')
    for identifier in pending + later:
        check(stack.whatsapp.count(identifier) == 1, f'{identifier} was sent {stack.whatsapp.count(identifier)} times')
        confirmed_by(stack, identifier, 'go')


def whatsapp_rows_adopted_with_identity(stack):
    """Each pending row moves with its provider identity, attempts, backoff and status, both ways."""
    stack.require_outbox_schema()
    for source, target in (('django', 'go'), ('go', 'django')):
        stack.reset()
        stack.set_authority(source, 1)
        fresh, = stack.queue_whatsapp(1)
        backoff, = stack.queue_whatsapp(1, attempts=1, error_code='131000', error_message='transient',
                                        next_attempt_offset='1 day')
        sent, = stack.queue_whatsapp(1, status='sent', attempts=1, provider_message_id=f'wamid.prior.{source}.1')
        failed, = stack.queue_whatsapp(1, status='failed', attempts=3, error_code='131026', error_message='undeliverable')
        delivered, = stack.queue_whatsapp(1, status='delivered', attempts=1, provider_message_id=f'wamid.prior.{source}.2')
        untouched = snapshot(stack, [backoff, sent, failed, delivered])
        api = stack.go_api() if 'go' in (source, target) else None
        guarded_switch(stack, source, 1, target)
        worker = None
        if target == 'django':
            worker = stack.django_worker()
            stack.django_ready()
            poll_django(stack)
        await_sent(stack, [fresh], f'{target} after activation')
        check(stack.whatsapp.count(fresh) == 1 and stack.delivery(fresh)['attempts'] == 1, f'{source}->{target}: fresh row sent once')
        consumer = outbox_consumer(stack, target) if target == 'go' else consumer_of(stack, 'django')[0]['consumer_id']
        heartbeats_after(stack, consumer, count=5)
        check(snapshot(stack, [backoff, sent, failed, delivered]) == untouched,
              f'{source}->{target}: rows not due or terminal changed: '
              f'{ {i: (untouched[i], stack.delivery(i)) for i in untouched if stack.delivery(i) != untouched[i]} }')
        stack.sql("UPDATE communications_whatsappdelivery SET next_attempt_at = now() - interval '1 second' WHERE id=%s", (backoff,))
        if target == 'django':
            poll_django(stack)
        await_sent(stack, [backoff], f'{target} (retry due)')
        row = stack.delivery(backoff)
        check(row['attempts'] == 2 and stack.whatsapp.count(backoff) == 1, f'{source}->{target}: the retry keeps its attempt count: {row}')
        for process in (api, worker):
            if process:
                stack.stop(process)


def legacy_polls_queued_at_the_switch(stack):
    """Already-queued Beat polls: a live one executes on Go (one bounded drain), an expired one
    ends REVOKED; the unsent row is delivered exactly once."""
    stack.require_outbox_schema()
    row, = stack.queue_whatsapp(1)
    live, expired = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    stack.push_poll(live, now + timedelta(hours=1))
    stack.push_poll(expired, now - timedelta(minutes=1))
    generation = guarded_switch(stack, 'django', 1, 'go')
    stack.go_consumer()
    stack.go_api()
    await_sent(stack, [row], 'Go after activation')
    wait_for(lambda: stack.work_row(live) and stack.work_row(live)['state'] in ('completed', 'expired', 'revoked', 'cancelled'),
             f'the live legacy poll {live} never reached a recorded terminal disposition on Go', timeout=60)
    check(stack.work_row(live)['state'] == 'completed' and stack.work_row(live)['owner_runtime'] == 'go',
          f'the live poll executes on Go: {stack.work_row(live)}')
    wait_for(lambda: stack.task_result(expired) == 'REVOKED', 'the expired legacy poll ends REVOKED (Celery expiry semantics)')
    check(stack.whatsapp.count(row) == 1, f'the row was sent {stack.whatsapp.count(row)} times')
    check(not stack.redis_copies(live) and not stack.redis_copies(expired), 'a legacy poll was handed back to a broker queue')
    check(generation == 2, 'generation')


def send_in_flight_at_the_freeze(stack):
    """A provider request in flight at the freeze finishes on the source; settle waits for it; no
    second send on the target."""
    stack.require_outbox_schema()
    row, = stack.queue_whatsapp(1)
    stack.whatsapp.hold(row)
    worker = stack.django_worker()
    stack.django_ready()
    poll_django(stack)
    wait_for(lambda: row in stack.whatsapp.in_flight, 'the Django delivery request never reached the provider')
    record, steps = stack.freeze('django', 1)
    settled = threading.Event()
    failure = []

    def settle():
        try:
            steps['settle'](record)
        except Exception as exc:
            failure.append(exc)
        settled.set()
    threading.Thread(target=settle, daemon=True).start()
    heartbeats_after(stack, consumer_of(stack, 'django')[0]['consumer_id'], count=3)
    check(not settled.is_set(), 'settle returned while a source delivery was in flight at the provider')
    stack.whatsapp.release(row)
    check(settled.wait(60) and not failure, f'settle after the in-flight send: {failure}')
    check(status(stack, row) == 'sent', f'the in-flight send completes on the source: {stack.delivery(row)}')
    confirmed_by(stack, row, 'django')
    stack.finish_switch(record, steps, stop=[worker])
    api_consumer = stack.go_api() and outbox_consumer(stack, 'go')
    heartbeats_after(stack, api_consumer, count=5)
    check(stack.whatsapp.count(row) == 1, f'the target re-sent a row the source sent: {stack.whatsapp.count(row)}')


def crash_after_provider_acceptance(stack):
    """A crash after the provider accepted, before the local commit: no automatic resend, the effect
    is ambiguous and blocks the switch until a delivery receipt (or the owner) reconciles it."""
    stack.require_outbox_schema()
    for source, target in (('django', 'go'), ('go', 'django')):
        stack.reset()
        stack.set_authority(source, 1)
        row, = stack.queue_whatsapp(1)
        stack.barrier.hold('effect:accepted', row)
        if source == 'django':
            process = stack.django_worker()
            stack.django_ready()
            poll_django(stack)
        else:
            process = stack.go_api()
        arrival = stack.barrier.wait_arrived('effect:accepted', row, f'the {source} deliverer after provider acceptance')
        check(stack.whatsapp.count(row) == 1, 'the provider accepted the send')
        stack.kill_tree(process, arrival)
        stack.barrier.reset()
        effect = stack.outbox_effects(row)
        check(len(effect) == 1 and effect[0]['state'] == 'intent' and effect[0]['runtime'] == source,
              f'{source}: the intent was committed before the provider call: {effect}')
        if source == 'django':
            stack.django_worker()
            stack.django_ready()
            poll_django(stack)
            consumer = consumer_of(stack, 'django')[0]['consumer_id']
        else:
            stack.go_api()
            consumer = outbox_consumer(stack, 'go')
        heartbeats_after(stack, consumer, count=5)
        check(stack.whatsapp.count(row) == 1, f'{source}: an unconfirmed send was replayed automatically')
        wait_for(lambda: [e['state'] for e in stack.outbox_effects(row)] == ['ambiguous'],
                 f'{source}: the unconfirmed effect is marked ambiguous: {stack.outbox_effects(row)}')
        steps = stack.handoff_steps()
        report = steps['preflight'](stack.record(uuid.uuid4(), source, 1, target))
        check({'task_id': f'whatsapp:{row}', 'reason': 'ambiguous_effect'} in report.get('blockers', []),
              f'{source}: preflight must name the ambiguous WhatsApp send: {report}')
        message = stack.whatsapp.message_id(row)
        code = stack.receipt_to_django(row, message) if source == 'django' else stack.receipt_to_go(row, message)
        check(code == 200, f'{source}: delivery receipt answered {code}')
        effect = stack.outbox_effects(row)
        check([e['state'] for e in effect] == ['reconciled_sent'] and effect[0]['provider_ref'] == message,
              f'{source}: a receipt reconciles the ambiguous send as sent: {effect}')
        check(stack.delivery(row)['status'] == 'delivered', f'{source}: row status {stack.delivery(row)}')
        report = steps['preflight'](stack.record(uuid.uuid4(), source, 1, target))
        check(not report.get('blockers'), f'{source}: no blocker after reconciliation: {report}')


def receipt_during_maintenance(stack):
    """A status callback reaching the frozen source is recorded or refused retryably; its redelivery
    to the target after activation is recorded once and history is retained."""
    stack.require_outbox_schema()
    row, = stack.queue_whatsapp(1)
    worker = stack.django_worker()
    stack.django_ready()
    poll_django(stack)
    await_sent(stack, [row], 'the Django worker')
    message = stack.whatsapp.message_id(row)
    record, steps = stack.freeze('django', 1)
    during = stack.receipt_to_django(row, message, 'delivered')
    check(during == 200 or 500 <= during < 600, f'a receipt during maintenance must be recorded (200) or retryable (5xx): {during}')
    check(len(stack.receipts_of(row)) == (1 if during == 200 else 0), 'a refused receipt wrote history; an accepted one did not')
    stack.finish_switch(record, steps, stop=[worker])
    stack.go_api()
    check(stack.receipt_to_go(row, message, 'delivered') == 200, 'the redelivered receipt is accepted by the target')
    check(stack.receipt_to_go(row, message, 'read') == 200, 'a later receipt is accepted by the target')
    check(stack.receipts_of(row) == [(message, 'delivered'), (message, 'read')],
          f'receipt history once per status: {stack.receipts_of(row)}')
    check(stack.delivery(row)['status'] == 'read' and stack.whatsapp.count(row) == 1, f'final row {stack.delivery(row)}')
    confirmed_by(stack, row, 'django')


def unsent_row_and_redundant_polls(stack):
    """Redundant polls are no-ops beside one pending row, on Django (Beat polls) and on Go (legacy
    poll envelopes executed by the Go consumer, never handed back)."""
    stack.require_outbox_schema()
    row, = stack.queue_whatsapp(1)
    worker = stack.django_worker()
    stack.django_ready()
    for _ in range(3):
        poll_django(stack)
    await_sent(stack, [row], 'the Django worker')
    wait_for(lambda: len([w for w in stack.sql("SELECT state FROM public.assozeta_handoff_work WHERE task_name=%s", (POLL,))
                          if w[0] == 'completed']) == 3, 'three Django polls complete')
    check(stack.whatsapp.count(row) == 1 and len(stack.outbox_effects(row)) == 1, 'one send, one effect')
    guarded_switch(stack, 'django', 1, 'go', stop=[worker])
    stack.go_consumer()
    stack.go_api()
    second, = stack.queue_whatsapp(1)
    polls = [str(uuid.uuid4()) for _ in range(3)]
    for task_id in polls:
        stack.push('assozeta-go', stack.envelope(POLL, task_id, queue='assozeta-go', stamp=time.time(),
                                                 expires=datetime.now(timezone.utc) + timedelta(hours=1)))
    await_sent(stack, [second], 'Go')
    wait_for(lambda: all(stack.work_row(t) and stack.work_row(t)['state'] == 'completed' for t in polls),
             'the Go consumer never completed the redundant polls (contract: Go handler for communications.tasks.deliver_whatsapp)',
             timeout=60)
    check(not any(stack.redis_copies(t) for t in polls), 'a poll was handed back to the Django queue')
    check(stack.whatsapp.count(second) == 1, f'the row was sent {stack.whatsapp.count(second)} times')


# -- realtime -----------------------------------------------------------------------------------
def delivered(stack, ids, runtime, who):
    def done():
        rows = {r['id']: r for r in stack.event_rows()}
        return all(rows[i]['delivered_at'] for i in ids) and rows
    rows = wait_for(done, f'{who} never delivered events {ids}', timeout=60)
    wrong = {i: rows[i]['delivered_runtime'] for i in ids if rows[i]['delivered_runtime'] != runtime}
    check(not wrong, f'events delivered by the wrong relay (expected {runtime}): {wrong}')


def received_once(stack, ids, target):
    wait_for(lambda: set(ids) <= set(stack.subscriber.event_ids()), f'the subscriber never received events {ids}')
    seen = stack.subscriber.event_ids()
    duplicates = sorted({i for i in ids if seen.count(i) > 1})
    check(not duplicates, f'events delivered more than once without a crash: {duplicates}')
    for item in stack.subscriber.messages():
        if item['message'].get('event_id') in ids:
            check(item['group'] == f'staff_board_{target}' and item['message'] == {
                'type': 'staff_board_changed', 'event_id': item['message']['event_id']},
                f'mirrored group message carries the event id and nothing else: {item}')


def realtime_only_the_effective_relay(stack):
    stack.require_outbox_schema()
    stack.subscriber.connect()
    django_relay = stack.django_relay()
    stack.go_relay()
    first, target = stack.add_events(5)
    delivered(stack, first, 'django', 'the Django relay')
    received_once(stack, first, target)
    guarded_switch(stack, 'django', 1, 'go', stop=[django_relay])
    second, _ = stack.add_events(3, target)
    delivered(stack, second, 'go', 'the Go relay')
    received_once(stack, first + second, target)


def relay_claim_released_before_activation(stack):
    stack.require_outbox_schema()
    stack.subscriber.connect()
    relay = stack.django_relay()
    (event,), target = stack.add_events(1)
    stack.barrier.hold('relay:claimed', str(event))
    stack.barrier.wait_arrived('relay:claimed', str(event), 'the Django relay claiming an event')
    closed, failure = threading.Event(), []
    result = {}

    def close():
        try:
            result['freeze'] = stack.freeze('django', 1)
        except Exception as exc:
            failure.append(exc)
        closed.set()
    threading.Thread(target=close, daemon=True).start()
    relay_consumer = consumer_of(stack, 'django')[0]['consumer_id']
    heartbeats_after(stack, relay_consumer, count=3)
    check(not closed.is_set(), 'the freeze completed while the source relay still held an event claim')
    stack.barrier.release('relay:claimed', str(event))
    check(closed.wait(60) and not failure, f'close_admission after the claim was released: {failure}')
    record, steps = result['freeze']
    stack.finish_switch(record, steps, stop=[relay])
    stack.go_relay()
    later, _ = stack.add_events(1, target)
    delivered(stack, [event], 'django', 'the Django relay (claim completed before its freeze acknowledgement)')
    delivered(stack, later, 'go', 'the Go relay')
    received_once(stack, [event] + later, target)


def relay_crash_after_send_is_redelivered_once(stack):
    stack.require_outbox_schema()
    stack.subscriber.connect()
    relay = stack.django_relay()
    (event,), target = stack.add_events(1)
    stack.barrier.hold('relay:sent', str(event))
    arrival = stack.barrier.wait_arrived('relay:sent', str(event), 'the Django relay after sending, before recording')
    wait_for(lambda: event in stack.subscriber.event_ids(), 'the first send reached the subscriber')
    stack.kill_tree(relay, arrival)
    stack.barrier.reset()
    guarded_switch(stack, 'django', 1, 'go')
    stack.go_relay()
    delivered(stack, [event], 'go', 'the Go relay (redelivery of the unrecorded event)')
    wait_for(lambda: stack.subscriber.event_ids().count(event) == 2, 'the unrecorded event is redelivered')
    copies = [m for m in stack.subscriber.messages() if m['message'].get('event_id') == event]
    check(len(copies) == 2 and copies[0] == copies[1], f'a redelivery is byte-identical and happens once: {copies}')


def subscriber_reconnects_across_the_switch(stack):
    stack.require_outbox_schema()
    stack.subscriber.connect()
    relay = stack.django_relay()
    before, target = stack.add_events(2)
    delivered(stack, before, 'django', 'the Django relay')
    received_once(stack, before, target)
    stack.subscriber.disconnect()
    record, steps = stack.freeze('django', 1)
    during, _ = stack.add_events(2, target)
    heartbeats_after(stack, consumer_of(stack, 'django')[0]['consumer_id'], count=5)
    check(not any(r['delivered_at'] for r in stack.event_rows() if r['id'] in during), 'a frozen relay delivered events')
    stack.finish_switch(record, steps, stop=[relay])
    stack.subscriber.connect()
    stack.go_relay()
    delivered(stack, during, 'go', 'the Go relay after activation')
    received_once(stack, before + during, target)
    check(stack.subscriber.connections == 2, 'the subscriber reconnected once')


SCENARIOS = {
    'whatsapp_single_owner_per_mode': whatsapp_single_owner_per_mode,
    'whatsapp_rows_adopted_with_identity': whatsapp_rows_adopted_with_identity,
    'legacy_polls_queued_at_the_switch': legacy_polls_queued_at_the_switch,
    'send_in_flight_at_the_freeze': send_in_flight_at_the_freeze,
    'crash_after_provider_acceptance': crash_after_provider_acceptance,
    'receipt_during_maintenance': receipt_during_maintenance,
    'unsent_row_and_redundant_polls': unsent_row_and_redundant_polls,
    'realtime_only_the_effective_relay': realtime_only_the_effective_relay,
    'relay_claim_released_before_activation': relay_claim_released_before_activation,
    'relay_crash_after_send_is_redelivered_once': relay_crash_after_send_is_redelivered_once,
    'subscriber_reconnects_across_the_switch': subscriber_reconnects_across_the_switch,
}
