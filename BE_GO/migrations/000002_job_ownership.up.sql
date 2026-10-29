-- Ownership is an operational fence, not an activation or Celery handoff API.
-- Ordinary installations continue using Django. No Go producer is enabled here.
CREATE TABLE public.assozeta_go_job_ownership (
    singleton smallint PRIMARY KEY CHECK (singleton = 1),
    runtime text NOT NULL CHECK (runtime IN ('django', 'go')),
    generation bigint NOT NULL CHECK (generation > 0)
);
INSERT INTO public.assozeta_go_job_ownership VALUES (1, 'django', 1);

-- Historical pending/running jobs have no valid ownership generation. Their
-- intent remains intact; only a fresh, authorized claim stamps a generation.
ALTER TABLE public.assozeta_go_jobs
    ADD COLUMN ownership_generation bigint NOT NULL DEFAULT 0 CHECK (ownership_generation >= 0);
