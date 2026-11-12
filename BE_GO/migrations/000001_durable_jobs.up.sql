-- Assozeta Go operational ownership only. No Django table or history is altered.
CREATE TABLE public.assozeta_go_jobs (
    id uuid PRIMARY KEY,
    association_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_.]{0,127}$'),
    version integer NOT NULL CHECK (version > 0),
    idempotency_key text NOT NULL CHECK (octet_length(idempotency_key) BETWEEN 1 AND 512),
    payload jsonb NOT NULL CHECK (octet_length(payload::text) <= 65536),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','completed','failed','cancelled')),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
    available_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    lease_owner text NOT NULL DEFAULT '',
    lease_token uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    lease_until timestamptz,
    error_code text NOT NULL DEFAULT '' CHECK (octet_length(error_code) <= 128),
    result jsonb CHECK (octet_length(result::text) <= 65536),
    UNIQUE (association_id,kind,idempotency_key),
    CHECK (attempts BETWEEN 0 AND max_attempts),
    CHECK ((state IN ('completed','failed','cancelled')) = (finished_at IS NOT NULL)),
    CHECK ((state='running' AND octet_length(lease_owner) BETWEEN 1 AND 128
              AND lease_token <> '00000000-0000-0000-0000-000000000000' AND lease_until IS NOT NULL)
        OR (state<>'running' AND lease_owner='' AND lease_token='00000000-0000-0000-0000-000000000000' AND lease_until IS NULL))
);
CREATE INDEX assozeta_go_jobs_pending ON public.assozeta_go_jobs (kind,available_at,id) WHERE state='pending';
CREATE INDEX assozeta_go_jobs_expired ON public.assozeta_go_jobs (kind,lease_until,id) WHERE state='running';
