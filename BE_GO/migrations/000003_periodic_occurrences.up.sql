-- Retained operational publication receipts. Django still owns the schedule
-- schema and its cursor; no schedule or ownership transition is seeded here.
CREATE TABLE public.assozeta_go_periodic_occurrences (
    id uuid PRIMARY KEY,
    periodic_task_id integer NOT NULL CHECK (periodic_task_id > 0),
    source_name text NOT NULL,
    dispatch_sequence bigint NOT NULL CHECK (dispatch_sequence > 0),
    job_id uuid NOT NULL UNIQUE REFERENCES public.assozeta_go_jobs(id) ON DELETE RESTRICT,
    dispatched_at timestamptz NOT NULL,
    ownership_generation bigint NOT NULL CHECK (ownership_generation > 0),
    definition_sha256 text NOT NULL CHECK (definition_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (periodic_task_id, dispatch_sequence)
);
-- No FK to PeriodicTask: deleting/recreating a Django source must not erase its
-- publication history or reset ordinals. No receipt pruning is supported.
-- Restoring an older database can rewind receipts, ordinals and generations.
-- These keys are not an across-restore fence: lifecycle leases and draining all
-- prior producers/consumers remain necessary before a database replacement.
