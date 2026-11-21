"""Phase 4, part B: WhatsApp outbox ownership and the realtime relay (D16).

CONTRACT P4-B. Builds on P3-A/P3-B and P4-A. Scenarios: TESTS/handoff/p4_scenarios_outbox.py,
stack: p4_outbox_stack.py. Boundary: private PostgreSQL + Redis, a local WhatsApp Cloud API stub
counting accepted sends by delivery id, real Django workers running
communications.tasks.deliver_whatsapp, the compiled Go API (cmd/server: embedded outbox poller
and status webhook), the Go consumer, both realtime relays (manage.py relay_go_realtime and
cmd/relay-go-realtime) and a subscriber on the cross-runtime mirror channel
'assozeta:realtime:v1' (notifications.realtime_layer; WebSocket transport reconnection itself is
pinned by Phase 2's test_p2_routing).

1. SCHEMA: BE_GO/migrations/000008_outbox_ownership.{up,down}.sql (down refuses),
   migrations.CurrentVersion = 8.
   public.assozeta_outbox_effects -- one row per provider send attempt of an outbox item
     outbox text ('whatsapp'); item_id text (the delivery id); attempt integer (the row's attempts
     after increment); PK (outbox, item_id, attempt); runtime text; ownership_generation bigint;
     consumer_id text; state text CHECK (intent|confirmed|ambiguous|reconciled_sent|
     reconciled_unsent); provider_ref text NULL (provider message id); recorded_at timestamptz;
     resolved_at timestamptz NULL; resolved_by text NULL
   public.assozeta_go_realtime_events + delivered_runtime text NULL, delivered_generation bigint NULL
   SQL: assozeta_outbox_claim(p_runtime, p_consumer_id, p_outbox) RETURNS TABLE(outcome, generation)
     owner | frozen | not_owner -- first statement of every delivery transaction (and of every
     relay claim transaction, outbox 'realtime'); holds the ownership row FOR SHARE.
   assozeta_outbox_effect_intent(p_runtime, p_consumer_id, p_outbox, p_item_id, p_attempt integer)
     RETURNS text: recorded | confirmed (already sent: do not send) | blocked (an unresolved intent
     or ambiguous effect exists for the item: do not send) -- committed BEFORE the provider call.
   assozeta_outbox_effect_confirm(p_outbox, p_item_id, p_attempt integer, p_provider_ref) -> 'confirmed'
   assozeta_outbox_reconcile(p_outbox, p_item_id, p_decision sent|not_sent, p_actor) -> text
     (sent: reconciled_sent, row keeps its status; not_sent: reconciled_unsent, the row is sendable once).

2. WHATSAPP (both runtimes; Django: communications.whatsapp.deliver_one under deliver_whatsapp; Go:
   internal/whatsapp DeliverOne under the embedded poller):
   - Ownership: a delivery transaction claims the outbox first; only the effective runtime with
     open admission sends. The Go API's embedded poller registers in assozeta_handoff_consumers
     (role 'outbox'; heartbeat env ASSOZETA_HANDOFF_HEARTBEAT, default 1s) and acknowledges a freeze
     once no delivery transaction is open; the Django side runs inside the registered worker.
   - Effects: intent (committed) -> provider call -> barrier effect:accepted (key = delivery id) ->
     confirm + row update in one commit. A row with an unconfirmed intent is never sent
     automatically again: the next deliverer marks the effect 'ambiguous' and leaves the row
     queued with unchanged attempts. A status callback (webhook, either runtime) for the item
     resolves an intent/ambiguous effect to reconciled_sent with the callback's provider id.
   - Controller: job_handoff preflight and verify report every ambiguous outbox effect as
     {'task_id': 'whatsapp:<delivery id>', 'reason': 'ambiguous_effect'} (TransferBlocked in verify).
   - Rows keep id, status, attempts, provider_message_id, error fields and next_attempt_at across a
     switch; backoff and terminal rows are untouched; nothing is re-sent.
   - Legacy polls: communications.tasks.deliver_whatsapp has a Go consumer handler (one bounded
     drain, <= 50 rows, same ownership/effect protocol); a queued poll transfers like any task
     (expired -> REVOKED, Celery semantics); in Go mode the deliver-whatsapp definition may fire
     through the Go scheduler and run that handler. Never handed back to the Django queue.
   - Test seam: Go API env ASSOZETA_HANDOFF_WHATSAPP_POLL (-tags handoff builds) overrides the
     10 s poll interval.

3. REALTIME (both relays): register (role 'relay'; Go flag --handoff-heartbeat DURATION, Django
   ASSOZETA_HANDOFF_HEARTBEAT_SECONDS); claim events only as the effective runtime with open
   admission (assozeta_outbox_claim(..., 'realtime') in the claim transaction); acknowledge a freeze
   only when no claim transaction is open (a claim held at the freeze completes or rolls back
   first); record delivered_runtime/delivered_generation with delivered_at. The group message
   carries the event id: {'type': 'staff_board_changed', 'event_id': <events.id>} (mirrored as
   {'version': 1, 'group': 'staff_board_<target>', 'message': ...}). Delivery stays at least once:
   a relay killed after sending and before recording lets the target redeliver the same event
   (same id, identical message) exactly once more. Barriers (key = event id): relay:claimed
   (inside the claim transaction, before sending), relay:sent (after group_send, before recording).

Commands: python3 -B -m unittest -v TESTS.handoff.test_p4_outbox_realtime (Phase 3 prerequisites).
Driver: $ASSOZETA_HANDOFF_PYTHON -B -m TESTS.handoff.p4_driver --suite outbox --report r.json [--only NAME].
"""
import unittest

from TESTS.handoff.p4_suite import ScenarioAssertions


class WhatsAppOutbox(ScenarioAssertions, unittest.TestCase):
    SUITE = 'outbox'

    def test_only_the_effective_runtime_delivers_before_and_after_a_switch(self):
        self.scenario('whatsapp_single_owner_per_mode')

    def test_pending_rows_keep_identity_attempts_backoff_and_status_in_both_directions(self):
        self.scenario('whatsapp_rows_adopted_with_identity')

    def test_already_queued_legacy_polls_have_an_explicit_outcome(self):
        self.scenario('legacy_polls_queued_at_the_switch')

    def test_a_send_in_flight_at_the_freeze_finishes_on_the_source_and_is_not_repeated(self):
        self.scenario('send_in_flight_at_the_freeze')

    def test_a_crash_after_provider_acceptance_blocks_replay_until_reconciled(self):
        self.scenario('crash_after_provider_acceptance')

    def test_a_receipt_arriving_during_maintenance_is_recorded_once(self):
        self.scenario('receipt_during_maintenance')

    def test_an_unsent_row_and_redundant_polls_send_once_in_both_modes(self):
        self.scenario('unsent_row_and_redundant_polls')


class RealtimeRelay(ScenarioAssertions, unittest.TestCase):
    SUITE = 'outbox'

    def test_only_the_effective_relay_delivers_events_with_their_ids(self):
        self.scenario('realtime_only_the_effective_relay')

    def test_the_source_relay_releases_its_claim_before_activation(self):
        self.scenario('relay_claim_released_before_activation')

    def test_an_unrecorded_send_is_redelivered_once_with_the_same_event_id(self):
        self.scenario('relay_crash_after_send_is_redelivered_once')

    def test_a_subscriber_reconnecting_across_the_switch_misses_no_event(self):
        self.scenario('subscriber_reconnects_across_the_switch')


if __name__ == '__main__':
    unittest.main()
