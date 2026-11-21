"""Phase 3, part B: external effects, the broker backlog, running attempts and every producer path.

CONTRACT P3-B. Builds on CONTRACT P3-A (TESTS/handoff/test_p3_database_job.py); this docstring
is the specification of what part B adds. Scenarios: p3_scenarios_{email,broker,durable,producers}.py.

1. SCHEMA (still migration 000006_job_handoff; nothing has been released)
   public.assozeta_handoff_effects -- one row per external effect of a logical attempt
     effect_key text PK (unique per job, retry number and effect within the attempt); task_id text NOT
     NULL REFERENCES work; attempt_id uuid NULL; kind text NOT NULL ('smtp', ...); state text CHECK
     (intent|confirmed|ambiguous|reconciled_sent|reconciled_unsent); provider_ref text NULL;
     recorded_at timestamptz NOT NULL; resolved_at timestamptz NULL; resolved_by text NULL
   assozeta_handoff_effect_intent(p_attempt_id uuid, p_effect_key text, p_kind text) RETURNS text:
     recorded | confirmed (already confirmed: do not send) | blocked (unresolved: do not send);
     SQLSTATE AZF01 when the attempt is stale. assozeta_handoff_effect_confirm(p_effect_key,
     p_provider_ref) RETURNS text ('confirmed').
   Claim outcomes gain blocked (work row blocked; no business code) and expired (expires passed: work
     state expired; the consumer stores Celery's REVOKED result, as for any expired message).
   Admitting an existing task id whose row is running by the same runtime (a Celery retry or the
     maintenance deferral of the same id) is accepted (open) or held (frozen) and updates the row in
     place (envelope, eta, expires, retries); finish('retried') keeps that state.
   accepted_at without an assozeta_published_at header is the epoch, so a completed restore still
     refuses the job exactly as CoordinatedTask refuses an unstamped message.

2. RUNTIMES
   Go consumer (cmd/celery-consumer): --handoff-heartbeat DURATION (role broker); every registered task
     claims through assozeta_handoff_claim first; never claims while frozen or not the effective
     runtime (stops reserving); a duplicate claim acks the transport copy with no effect. Barriers:
     reservation:popped (after BRPOP, before the unacked bookkeeping; key = header id),
     reservation:confirmed, claim:granted, claim:refused, effect:accepted, attempt:fenced.
     Its retries/republishes are admitted like Django retries.
   Email tasks (core.tasks.send_mail_async, send_email_text/template/html) on both runtimes record an
     effect intent before each SMTP transaction and confirm it after the provider accepted (barrier
     effect:accepted in between). A confirmed effect is never resent; an intent is never resent
     automatically.
   Go broker producer (celery.Producer.Publish as wired in cmd/worker, cmd/server, cmd/celery-consumer)
     admits like the Django producer (runtime go, transport broker): rejected -> error, nothing pushed;
     held -> nothing pushed.
   Broker representation: a Go target delivers tasks that have a Go consumer handler to 'assozeta-go';
     a Django target to 'celery'. The priority subqueue (kombu steps 0/3/6/9 from properties.priority)
     and FIFO order within every source subqueue are preserved. Unrelated Redis keys are untouched.

3. CONTROLLER (selfhost/updater/job_handoff.py)
   handoff_steps(...) returns all six runtime_switch.STEP_STAGES: preflight, close_admission, settle,
     stage_backlog, activate, verify (Phase 5 maps them 1:1; commit stays PsqlAuthority.commit).
   class TransferBlocked(Exception): .blockers = [{'task_id', 'reason'}] (opaque reference and reason,
     never task arguments). Reasons: unknown_task, no_target_handler, unsupported_encoding,
     unsupported_option, ambiguous_effect, running.
   preflight(record) -> {'blockers': [...]}: read-only (no ledger row written, broker unchanged).
   close_admission ignores consumers silent for longer than heartbeat_grace (settle proves them fenced).
   settle(record): waits for running source attempts; raises TransferBlocked(task, 'running') at
     settle_timeout. An attempt of a silent consumer is released (outcome released, job pending) only
     when it is fenced: Django attempts at once (fence requires a running attempt), Go durable attempts
     after lease_until. An attempt with an unconfirmed effect intent makes the effect ambiguous and
     the job blocked ('ambiguous_effect').
   stage_backlog(record): resumable and idempotent (one receipt per job; a rerun finishes removing
     source copies of rows it already staged); adopts broker items and Go rows that have no ledger
     row; after each receipt removes the source copy wherever it is then (lists, priority subqueues,
     unacked hash + index), so a concurrent visibility restore cannot leave one behind; unsupported
     items become state blocked with block_reason, envelope preserved byte for byte, removed from the
     source queue (no poison loop), and are raised together as TransferBlocked after the rest is
     staged; batch_size bounds every read.
   activate(record): idempotent on rerun (a duplicated envelope is deduplicated by the claim).
   verify(record) -> {'outstanding_source', 'undelivered', 'blocked'}; TransferBlocked unless all zero.
   abort(store, broker, record, *, barrier=None): before commit only; staged and held jobs of the
     operation return to the source generation and representation; admission reopens.
   reconcile(store, task_id, decision, actor): 'sent' (ambiguous -> reconciled_sent, job completed,
     nothing resent) | 'not_sent' (-> reconciled_unsent, job pending: one replay) | 'discard' (a
     blocked job -> cancelled). Records resolved_by and resolved_at.
   CLI: python3 -B selfhost/updater/job_handoff.py STEP --record JSON --psql JSON(argv) --redis
     JSON({host, port, username, password, db}) [--barrier-dir DIR] [--batch-size N]
     [--freeze-timeout S] [--settle-timeout S] [--heartbeat-grace S]; STEP preflight|close-admission|
     settle|stage-backlog|activate|verify|abort; prints one JSON line; exit 0, 3 for TransferBlocked
     ({"blocked": [...]}), 1 otherwise. --barrier-dir enables receipt:persisted, source:acked and
     destination:published (file protocol of P3-A).

4. PRODUCERS: every Django publication API and the Go task children are admitted (test_p3_producers);
   at Phase 3 exit no publisher entry of handoff-inventory.json may still have an owner planned for
   phase 3.

Commands: python3 -B -m unittest -v TESTS.handoff.test_p3_email_job TESTS.handoff.test_p3_broker_backlog
TESTS.handoff.test_p3_durable_jobs TESTS.handoff.test_p3_producers (same prerequisites as P3-A).
"""
import unittest

from TESTS.handoff.p3_suite import ScenarioAssertions


class EmailJob(ScenarioAssertions, unittest.TestCase):
    SUITE = 'email'

    def test_django_email_transfers_and_sends_once_on_the_go_consumer(self):
        self.scenario('forward_runs_once_on_go_consumer')

    def test_go_email_transfers_and_sends_once_on_django(self):
        self.scenario('reverse_runs_once_on_django')

    def test_stale_go_consumer_cannot_claim_while_django_owns(self):
        self.scenario('stale_go_consumer_cannot_claim')

    def test_go_consumer_receives_zero_claims_after_its_freeze_acknowledgement(self):
        self.scenario('go_consumer_freeze_ack_zero_claims')

    def test_crash_before_the_send_is_a_safe_retry(self):
        self.scenario('crash_before_send_is_a_safe_retry')

    def test_ambiguous_smtp_send_blocks_replay_and_success_until_reconciled_django_source(self):
        self.scenario('ambiguous_forward')

    def test_ambiguous_smtp_send_blocks_until_reconciled_go_source(self):
        self.scenario('ambiguous_reverse')


if __name__ == '__main__':
    unittest.main()
