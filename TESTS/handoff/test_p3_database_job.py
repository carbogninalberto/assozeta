"""Phase 3, part A: accepted work is recoverable and portable, first slice (a database-only task).

CONTRACT P3-A. This docstring is the specification; the scenarios are in
TESTS/handoff/p3_scenarios_db.py, the stack in p3_stack.py, the oracle in p3_oracle.py.
Boundary: private PostgreSQL + private Redis, a real Django producer (Task.apply_async),
real ``celery worker -P prefork`` processes, the compiled Go durable worker (cmd/worker) and
a compiled Go durable producer (cmd/handoff-probe -> repository.Jobs.Enqueue), the updater
transfer module. Slice task: ``reset_communication_daily_email_balance`` (Django
application.tasks.reset_email_balance; Go job kind of the same name, payload {}, instance-wide
association uuid.Nil). Its business effect is one UPDATE statement, counted per process.

1. SCHEMA: BE_GO/migrations/000006_job_handoff.{up,down}.sql (down refuses, like 000005),
   migrations.CurrentVersion = 6. Existing tables, task ids, expiry, result backend and the
   assozeta_published_at stamp are preserved. Additions:

   public.assozeta_go_job_ownership + admission text NOT NULL DEFAULT 'open' CHECK (open|frozen),
     admission_operation_id uuid NULL (the freezing operation; NULL when open).
   public.assozeta_handoff_work  -- one row per accepted logical job, either runtime/transport
     task_id text PK (Celery task id / Go job id as canonical lowercase uuid text, 1..255 octets)
     task_name text NOT NULL; transport text NOT NULL CHECK (broker|sql): the current representation
     queue text NULL (broker queue); envelope bytea NULL (exact broker payload; NULL for sql)
     origin_runtime text NOT NULL; owner_runtime text NULL; owner_generation bigint NULL (NULL while held)
     operation_id uuid NULL (operation that holds/staged it); state text NOT NULL CHECK
       (pending|held|staged|running|completed|failed|cancelled|expired|revoked|blocked)
     accepted_at timestamptz NOT NULL (= the data-restore stamp: assozeta_published_at for broker
       work, the Go row created_at for sql work); eta, expires timestamptz NULL; parent_id, root_id
       text NULL; retries integer NOT NULL DEFAULT 0; priority smallint NULL; association_id uuid NULL;
       block_reason text NULL; updated_at timestamptz NOT NULL
   public.assozeta_handoff_attempts  -- attempts, never logical completions
     attempt_id uuid PK; task_id text NOT NULL REFERENCES work; runtime text; generation bigint;
     consumer_id text; retries integer; claimed_at timestamptz; finished_at timestamptz NULL;
     outcome text CHECK (running|completed|failed|retried|fenced|released)
   public.assozeta_handoff_transfers  -- one receipt per (operation, job) moved between runtimes
     operation_id uuid, task_id text, PK (operation_id, task_id); source_runtime text;
     source_generation bigint; target_runtime text; target_generation bigint NULL (set by commit);
     source_location text CHECK (ledger|queue|unacked|sql) (where the source copy was found);
     staged_at timestamptz; source_removed_at, delivered_at timestamptz NULL; delivery text NULL
     ('queue:<name>' | 'sql:<job id>')
   public.assozeta_handoff_consumers  -- every claiming process registers and heartbeats
     consumer_id text PK (opaque, unique per process); runtime text; role text CHECK (broker|durable);
     registered_at, heartbeat_at timestamptz; heartbeat_seq bigint (incremented per heartbeat);
     freeze_operation_id uuid NULL; frozen_at timestamptz NULL

2. SHARED SQL (one implementation for Django, both Go processes and the updater). Every
   function locks the ownership row FOR SHARE (commit takes it FOR UPDATE, so it waits for
   admitted transactions):
   assozeta_handoff_admit(p_runtime, p_task_id, p_task_name, p_transport, p_queue, p_envelope bytea,
       p_accepted_at timestamptz, p_eta timestamptz, p_expires timestamptz) RETURNS TABLE(outcome, generation)
     accepted (p_runtime is effective, admission open: row pending, owner = p_runtime/generation;
       the caller may publish) | held (admission frozen, p_runtime is the source: row held with
       operation_id; the caller must NOT publish) | rejected (p_runtime is not effective: no row;
       the caller fails retryably) | duplicate (task id exists: no change, no second publish).
       parent_id, root_id, retries and priority are read from a broker envelope's headers.
   assozeta_handoff_claim(p_runtime, p_consumer_id, p_task_id, p_task_name, p_envelope bytea)
       RETURNS TABLE(outcome, attempt_id uuid, generation)
     claimed (pending, owned by p_runtime at the current generation, admission open: state running,
       attempt row inserted) | frozen | not_owner | duplicate (running or terminal). An unknown
       task id is adopted (inbox receipt; p_envelope may be NULL) and claimed when p_runtime is
       effective and admission is open, so historical and test envelopes keep running.
   assozeta_handoff_fence(p_attempt_id uuid) RETURNS bigint -- first statement of a fenced
       effect transaction; SQLSTATE 'AZF01' unless the attempt is running at the current
       generation of the effective runtime.
   assozeta_handoff_finish(p_attempt_id uuid, p_outcome text) RETURNS text -- completed|failed|
       retried|released; same fence and SQLSTATE 'AZF01'; updates the work row.
   (Recording a fenced attempt's outcome after its rollback is the implementation's choice, e.g.
       a small assozeta_handoff_record_fenced(uuid); the oracle reads attempts.outcome.)
   assozeta_handoff_heartbeat(p_consumer_id, p_runtime, p_role) RETURNS TABLE(runtime, generation,
       admission, operation_id) -- upserts the consumer row and increments heartbeat_seq.
   assozeta_handoff_acknowledge_freeze(p_consumer_id, p_operation_id uuid) RETURNS text ('acknowledged')
   assozeta_handoff_close_admission(p_operation_id uuid, p_runtime, p_generation bigint) RETURNS text
       closed | already_closed | conflict (source/generation mismatch or another operation froze it)
   assozeta_commit_runtime_authority (Phase 1 signature unchanged; still callable without a freeze)
       additionally, in the same transaction when committed: binds every held/staged row of the
       operation to the target (state pending, owner = target/new generation), stamps
       transfers.target_generation and reopens admission.

3. RUNTIME INTEGRATION
   Django producer: every publication of a covered task (Task.apply_async, delay, send_task, retry,
     Beat) admits first. rejected -> raise core.handoff.AdmissionRejected (retryable, nothing
     published); held -> return normally, nothing published; inside a transaction the admission
     joins it and the publish runs on commit. Only publications are admitted: kombu's own
     re-puts (e.g. "Requeuing undeliverable message" after a consumer cancel, restores of unacked
     messages) must pass through untouched, or the copy is lost. Settings ASSOZETA_JOB_HANDOFF
     is not needed: the hooks are active when the database is PostgreSQL and the 000006 functions
     exist; otherwise (SQLite unit tests, a database without the migration) behaviour is legacy.
   Django worker: registers (role broker) and heartbeats every ASSOZETA_HANDOFF_HEARTBEAT_SECONDS
     (setting, default 1.0); on a freeze it stops reserving from the broker and acknowledges; when
     it is not the effective runtime it reserves nothing. Each execution claims first (frozen /
     not_owner / duplicate -> no business code, barrier claim:refused). The slice task runs its
     effect and the finish in one transaction opened by assozeta_handoff_fence; a fenced attempt
     rolls back (barrier attempt:fenced) and is recorded outcome 'fenced'.
   Go durable worker (cmd/worker): new flag --handoff-heartbeat DURATION (default 1s), role durable.
     Claims only rows whose work row is pending and owned by go at the current generation, never
     while frozen; records attempts; the slice task fences effects as today and finishes the
     attempt in the effect transaction. Jobs.Enqueue admits inside the caller's transaction
     (transport sql): rejected -> error text contains "handoff admission rejected", nothing written;
     held -> the row is written but not claimable until commit.
   Representations (activate): Django -> Go: an assozeta_go_jobs row with id = task id, kind = task
     name, version 1, payload {}, association uuid.Nil, idempotency_key 'handoff:'||task_id,
     max_attempts 3, attempts = retries, available_at = eta or accepted_at, created_at = accepted_at.
     Go -> Django: a Celery protocol 2 JSON envelope LPUSHed to 'celery' (exchange '', routing key
     celery) with id = root_id = job id, parent_id null, task = kind, args [] kwargs {}, retries =
     attempts, eta = available_at when in the future else null, assozeta_published_at =
     epoch(created_at); the Go row becomes state 'cancelled', error_code 'handoff_transferred'.

4. CONTROLLER SEAMS: selfhost/updater/job_handoff.py (stdlib only; never starts an application
   process): PsqlStore(command, environment=None) (argv prefix, SQL on stdin, like PsqlAuthority);
   RespBroker(host, port, *, username=None, password=None, db=0, timeout=10.0) (RESP2 over a
   socket); handoff_steps(store, broker, *, barrier=None, batch_size=500, freeze_timeout=60.0,
   settle_timeout=60.0, heartbeat_grace=5.0) -> {'close_admission', 'settle', 'stage_backlog',
   'activate'}, each a callable(record) taking the runtime_switch journal record ({id,
   previous_runtime, source_generation, target}) and returning a JSON-able summary, so Phase 5
   merges them into runtime_switch.default_steps. close_admission returns only after every live
   source consumer acknowledged the freeze; settle returns when no source attempt is running;
   stage_backlog stages every source-owned pending job (ledger first, then removes broker copies
   from the source queues) with a transfer receipt; commit is runtime_switch's authority.commit;
   activate delivers the bound work in the target representation and records delivery.

5. BARRIERS (test builds only: Go -tags handoff with env ASSOZETA_HANDOFF_BARRIER_DIR; Django
   setting ASSOZETA_HANDOFF_BARRIER_DIR; updater barrier callable(point, key)). On passing
   point P for key K a process writes D/arrived.P.K (JSON {pid, point, key}, atomically by
   rename); then, if D/hold.P.K or D/hold.P.all exists, it waits until D/release.P.K or
   D/release.P.all exists (poll <= 50 ms, give up after 120 s). Points pinned here (key = task id
   unless noted): reservation:confirmed (consumer holds the delivery, before claiming),
   claim:granted (claim committed, before the effect), claim:refused, attempt:fenced,
   freeze:acknowledged (key = consumer id), receipt:persisted, source:acked,
   destination:published (updater). Barriers never hold a database lock.

Commands: python3 -B -m TESTS.handoff.run_expected_red (tracks the reds), or
python3 -B -m unittest -v TESTS.handoff.test_p3_database_job. Needs PostgreSQL server binaries
with psql, redis-server, Go and Python BE/Dockerfile major.minor with BE requirements
(ASSOZETA_HANDOFF_PYTHON when not the running interpreter). ~3-6 min.
"""
import unittest

from TESTS.handoff.p3_suite import ScenarioAssertions


class HandoffSchemaContract(ScenarioAssertions, unittest.TestCase):
    SUITE = 'db'

    def test_migration_6_adds_the_ledger_tables_and_shared_functions(self):
        self.scenario('schema_contract')

    def test_shared_sql_admits_holds_claims_fences_and_binds_at_commit(self):
        self.scenario('sql_functions')


class DatabaseJobDjangoToGo(ScenarioAssertions, unittest.TestCase):
    SUITE = 'db'

    def test_stale_django_producer_cannot_publish_after_commit(self):
        self.scenario('forward_stale_producer')

    def test_stale_django_worker_cannot_claim_after_commit(self):
        self.scenario('forward_stale_worker_claim')

    def test_stale_django_attempt_cannot_complete_after_commit(self):
        self.scenario('forward_stale_worker_complete')

    def test_before_during_after_the_switch_every_accepted_job_runs_once(self):
        self.scenario('forward_d01')


class DatabaseJobGoToDjango(ScenarioAssertions, unittest.TestCase):
    SUITE = 'db'

    def test_stale_go_producer_cannot_enqueue_after_commit(self):
        self.scenario('reverse_stale_producer')

    def test_stale_go_attempt_cannot_complete_after_commit(self):
        self.scenario('reverse_stale_worker_complete')

    def test_go_created_rows_without_envelope_cross_once_and_future_stays_future(self):
        self.scenario('reverse_d01')


if __name__ == '__main__':
    unittest.main()
