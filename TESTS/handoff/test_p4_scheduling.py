"""Phase 4, part A: scheduling authority in both runtimes.

CONTRACT P4-A. This docstring is the specification; scenarios are in
TESTS/handoff/p4_scenarios_sched.py, the stack in p4_stack.py (built on the Phase 3 stack,
whose ledger, admission, freeze and controller seams -- CONTRACT P3-A/P3-B -- are reused, never
duplicated). Boundary: private PostgreSQL + Redis, real Beat processes (InstanceScheduler),
the compiled Go scheduler (cmd/scheduler), real Django workers, the Go durable worker and Go
consumer, the Phase 3 transfer module and the Phase 1 authority commit. Plan: phase 4 of
implementation-phases.md, job-handoff.md "Schedules and recovery" and "Scheduled definition or
due occurrence", tdd-and-release.md D08.

1. SCHEMA: BE_GO/migrations/000007_schedule_authority.{up,down}.sql (down refuses, like 000005/6),
   migrations.CurrentVersion = 7. Additions:
   public.assozeta_periodic_seed -- one row per declared definition ever seeded
     name text PK; task text NOT NULL; definition jsonb NOT NULL (the canonical declaration, 4.);
     definition_sha256 text NOT NULL; seeded_at timestamptz NOT NULL;
     seeded_by text NOT NULL CHECK (django|go|migration)
   public.assozeta_scheduler_lease -- the single scheduler authority (singleton smallint PK = 1)
     runtime text; holder text (opaque, unique per scheduler process); scheduler_generation bigint
     NOT NULL (strictly increasing, never reused); ownership_generation bigint (the authority
     generation it was acquired under); acquired_at, heartbeat_at, lease_until timestamptz
   public.assozeta_go_periodic_occurrences (existing receipts) becomes the receipt table of BOTH
     runtimes: + runtime text NOT NULL CHECK (django|go) (existing rows 'go'); + task_id text UNIQUE
     (the occurrence's logical job = assozeta_handoff_work.task_id; existing rows = job_id::text);
     + scheduler_generation bigint; job_id becomes NULL-able (a Django occurrence has no Go row).
     ownership_generation stays the authority generation; (periodic_task_id, dispatch_sequence)
     stays unique and is the occurrence identity.
   assozeta_handoff_consumers.role CHECK widened to broker|durable|scheduler|outbox|relay.

2. SHARED SQL (one implementation; every function locks the ownership row FOR SHARE):
   assozeta_seed_periodic_definitions(p_runtime text, p_definitions jsonb) RETURNS TABLE(name, outcome)
     per declaration: created (no seed record, no row: definition + schedule rows inserted, seed
     recorded) | adopted (no seed record, a row of that name exists: recorded, row untouched) |
     kept (seed record with the same definition) | updated (the declaration changed and the row
     still equals the previously seeded definition: row follows the new declaration) |
     kept_custom (declaration changed, row customized: row untouched, new declaration recorded) |
     kept_deleted (seed record exists, row deleted by the owner: never re-created). Bumps
     django_celery_beat_periodictasks.last_update when it changes a row. Never touches enabled,
     last_run_at, total_run_count, one_off, start_time or rows that are not declared.
   assozeta_scheduler_acquire(p_runtime text, p_holder text, p_lease_seconds double precision)
       RETURNS TABLE(outcome, scheduler_generation)
     acquired (lease free or expired -> new generation = previous + 1, holder = p_holder) | renewed
     (same holder, lease extended) | held (another holder's lease is live) | not_owner (p_runtime is
     not effective) | frozen (admission frozen). Lease expiry uses the database clock.
   assozeta_scheduler_fence(p_holder text, p_scheduler_generation bigint) RETURNS bigint (the
     ownership generation) -- first statement of every publication transaction; SQLSTATE 'AZF02'
     unless holder and scheduler generation are current, the lease is live, the holder's runtime
     is effective and admission is open.

3. BOTH SCHEDULERS (Beat's InstanceScheduler and cmd/scheduler):
   - Register in assozeta_handoff_consumers (role 'scheduler') through assozeta_handoff_heartbeat
     and acknowledge a freeze (assozeta_handoff_acknowledge_freeze) only when no publication
     transaction is open; Phase 3's close_admission therefore waits for them.
   - Hold the lease (acquire/renew every heartbeat). Only the lease holder of the effective
     runtime publishes. A missing heartbeat never fences by itself: the fence inside the
     publication transaction does. Settings: Go flags --lease DURATION (default 30s) and
     --handoff-heartbeat DURATION (default 1s); Django ASSOZETA_SCHEDULER_LEASE_SECONDS (default
     30) and ASSOZETA_HANDOFF_HEARTBEAT_SECONDS.
   - Seed at startup through assozeta_seed_periodic_definitions with the embedded declarations
     (Django: instance.scheduler.periodic_declarations(); Go: the same list, printed by
     ``cmd/scheduler --print-declarations``); Beat no longer overwrites rows on start
     (update_from_dict). A non-effective scheduler may seed (idempotent) but never publishes.
   - Publication transaction, per due occurrence: fence; lock the PeriodicTask row; re-evaluate
     due from the persisted cursor (last_run_at / date_changed / start_time / total_run_count) at
     the scheduler clock; dispatch_sequence = previous + 1; admit the job through the Phase 3
     admission (Django: broker envelope, new task id; Go: Jobs.Enqueue for a native kind, the
     admitted broker producer for a task only the Go consumer handles, e.g.
     instance.backups.notify_failures -> queue 'assozeta-go'); insert the receipt (runtime,
     task_id, scheduler_generation, ownership_generation, definition_sha256, dispatched_at = the
     scheduler clock instant); advance last_run_at to that instant and total_run_count + 1 (a
     one-off is disabled after its run, as Beat does); commit; then push a broker job. A crash
     between commit and push: the restarted scheduler publishes the admitted job from the ledger
     exactly once and never records a second occurrence.
   - Missed-tick policy (Beat's, both runtimes): a definition found due after any gap (switch,
     freeze, downtime) fires once at the instant it is evaluated; missed slots are not replayed.
   - Evaluator parity: every persisted definition Beat evaluates (interval, crontab in any
     timezone including DST gap/fold, day_of_week, day_of_month/month_of_year, clocked one-off,
     start_time, expires/expire_seconds) produces the same occurrences on Go
     (test_p4_go_units: schedule_parity_handoff_test.go; domain.PeriodicSchedule gains
     ClockedTime *time.Time and TotalRunCount int64).
   - celery.backend_cleanup has a Go executor in Go mode (result retention: rows of
     django_celery_results_taskresult with date_done older than result_expires = 1 day), fired by
     the Go scheduler from the same seeded definition (fresh and upgraded installs).

4. CANONICAL DECLARATION (seed input, periodic_declarations(), --print-declarations): a JSON array
   of {name, task, schedule, args (array), kwargs (object), options}; schedule is
   {kind:'interval', every:int, period:'seconds'|'minutes'|...} or {kind:'crontab', minute, hour,
   day_of_week, day_of_month, month_of_year, timezone}; options has exactly the keys queue,
   exchange, routing_key, priority, expire_seconds, expires (null when unset) and headers ({}).
   The list is app.conf.beat_schedule plus Celery's celery.backend_cleanup (crontab 0 4, options
   expire_seconds 43200).

5. TEST SEAMS (inert unless set):
   Clock: Go ``-tags handoff`` builds of cmd/scheduler read env ASSOZETA_HANDOFF_CLOCK_FILE; Beat
     reads setting ASSOZETA_HANDOFF_CLOCK_FILE. When set, the file's single ISO 8601 instant is the
     scheduler's "now" for due evaluation (including Beat's crontab pre-filter), cursors,
     date_changed written by seeding and dispatched_at; job availability, message ETA/expiry and
     the lease stay on the real clock.
   Barriers (file protocol of P3-A; Django via core.handoff.barrier):
     schedule:ticked   key = epoch milliseconds of the clock instant; after a complete evaluation
                       pass at that instant (publications committed), also when nothing was due,
                       the runtime is not effective or admission is frozen; once per (process, instant)
     schedule:due      key = '<scheduler_generation>-<periodic_task_id>'; after deciding an
                       occurrence is due, before its publication transaction begins (no lock held)
     schedule:committed key = '<periodic_task_id>'; after the publication commit, before any push
     schedule:fenced   key = '<scheduler_generation>-<periodic_task_id>'; after the fence refused a
                       publication (stale generation, lost lease, frozen, not effective)

Commands: python3 -B -m unittest -v TESTS.handoff.test_p4_scheduling TESTS.handoff.test_p4_go_units
(same prerequisites as Phase 3: ASSOZETA_HANDOFF_PYTHON, PostgreSQL, redis-server, Go). Driver:
$ASSOZETA_HANDOFF_PYTHON -B -m TESTS.handoff.p4_driver --suite sched --report r.json [--only NAME].
"""
import unittest

from TESTS.handoff.p4_suite import ScenarioAssertions


class SeedingAndDefinitions(ScenarioAssertions, unittest.TestCase):
    SUITE = 'sched'

    def test_django_and_go_embed_one_canonical_declaration_list(self):
        self.scenario('declarations_match')

    def test_fresh_database_is_seeded_once_and_a_restart_reseeds_nothing(self):
        self.scenario('seed_once_fresh_beat')

    def test_go_on_a_fresh_database_where_beat_never_ran_seeds_what_beat_would(self):
        self.scenario('seed_fresh_go_equals_beat')

    def test_customized_disabled_deleted_one_off_and_custom_definitions_survive_restart_and_both_switches(self):
        self.scenario('customized_definitions_survive_restart_and_both_switches')

    def test_a_changed_declaration_updates_only_uncustomized_definitions(self):
        self.scenario('seed_release_update_respects_customization')


class OccurrenceAuthority(ScenarioAssertions, unittest.TestCase):
    SUITE = 'sched'

    def test_beat_commits_receipt_job_and_cursor_together_and_a_crash_before_push_publishes_once(self):
        self.scenario('beat_receipt_cursor_and_crash_before_push')

    def test_occurrence_due_at_the_freeze_fires_once_on_go(self):
        self.scenario('freeze_before_publication_forward')

    def test_occurrence_due_at_the_freeze_fires_once_on_django(self):
        self.scenario('freeze_before_publication_reverse')

    def test_occurrence_committed_before_the_freeze_runs_once_on_go_and_is_not_republished(self):
        self.scenario('freeze_after_commit_forward')

    def test_occurrence_committed_before_the_freeze_runs_once_on_django_and_is_not_republished(self):
        self.scenario('freeze_after_commit_reverse')

    def test_go_on_a_fresh_database_where_beat_never_ran_publishes_and_executes(self):
        self.scenario('fresh_go_where_beat_never_ran')

    def test_switching_back_before_the_next_due_time_fires_once_on_django(self):
        self.scenario('switch_back_before_next_due')

    def test_missed_ticks_are_applied_once_on_resume(self):
        self.scenario('missed_ticks_once_on_resume')


class DefinitionTypes(ScenarioAssertions, unittest.TestCase):
    SUITE = 'sched'

    def test_dst_forward_and_repeated_local_times_match_beat_across_a_switch(self):
        self.scenario('dst_forward_and_repeated_local_times')

    def test_interval_and_cron_types_match_beat_across_a_switch(self):
        self.scenario('interval_and_cron_types')

    def test_definition_edited_during_the_transition_applies_on_the_target(self):
        self.scenario('edited_definition_during_transition')

    def test_disabled_never_fires_and_one_off_fires_once_in_both_directions(self):
        self.scenario('disabled_and_one_off_definitions')


class SchedulerGenerations(ScenarioAssertions, unittest.TestCase):
    SUITE = 'sched'

    def test_lost_go_scheduler_heartbeat_cannot_admit_a_second_generation(self):
        self.scenario('lost_heartbeat_go')

    def test_lost_beat_heartbeat_cannot_admit_a_second_generation(self):
        self.scenario('lost_heartbeat_django')


class ScheduledExceptions(ScenarioAssertions, unittest.TestCase):
    SUITE = 'sched'

    def test_backup_failure_notice_occurrence_is_counted_once_across_a_scheduler_crash(self):
        self.scenario('backup_notifications_counted_once_across_crash')

    def test_result_retention_runs_in_go_mode_on_fresh_and_upgraded_installs(self):
        self.scenario('backend_cleanup_on_go')


if __name__ == '__main__':
    unittest.main()
