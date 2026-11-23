-- Return one snapshot of the authority and its progress signals. Older databases
-- can lack individual operational tables while a distribution is being upgraded.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '5s';
SELECT to_regclass('public.assozeta_go_job_ownership') IS NOT NULL AS has_owner,
       to_regclass('public.assozeta_go_schema_migrations') IS NOT NULL AS has_schema,
       to_regclass('public.assozeta_handoff_consumers') IS NOT NULL AS has_consumers,
       to_regclass('public.assozeta_handoff_work') IS NOT NULL AS has_work,
       to_regclass('public.assozeta_scheduler_lease') IS NOT NULL AS has_scheduler,
       to_regclass('public.assozeta_go_realtime_events') IS NOT NULL AS has_events
\gset
\set runtime unknown
\set generation null
\set schema schema_missing
\set worker no_heartbeat
\set scheduler no_lease
\set relay no_heartbeat
\if :has_owner
SELECT coalesce(max(runtime), 'unknown') AS runtime, coalesce(max(generation)::text, 'null') AS generation
FROM public.assozeta_go_job_ownership WHERE singleton = 1
\gset
\endif
\if :has_schema
SELECT CASE WHEN count(*) = 0 THEN 'schema_missing' WHEN bool_or(dirty) THEN 'schema_dirty' ELSE 'ok' END AS schema
FROM public.assozeta_go_schema_migrations
\gset
\endif
\if :has_consumers
WITH signals AS (
    SELECT runtime, role, heartbeat_at BETWEEN now() - make_interval(secs => :'heartbeat'::double precision)
                                          AND now() AS fresh
    FROM public.assozeta_handoff_consumers
), required(role) AS (
    SELECT 'broker' UNION ALL SELECT 'durable' WHERE :'runtime' = 'go'
)
SELECT CASE WHEN EXISTS (SELECT FROM signals WHERE runtime <> :'runtime' AND role IN ('broker','durable') AND fresh)
                THEN 'inactive_runtime_active'
            WHEN EXISTS (SELECT role FROM required EXCEPT SELECT role FROM signals WHERE runtime = :'runtime')
                THEN 'no_heartbeat'
            WHEN EXISTS (SELECT role FROM required EXCEPT SELECT role FROM signals WHERE runtime = :'runtime' AND fresh)
                THEN 'stale_heartbeat' ELSE 'ok' END AS worker,
       CASE WHEN EXISTS (SELECT FROM signals WHERE runtime <> :'runtime' AND role = 'relay' AND fresh)
                THEN 'inactive_runtime_active'
            WHEN NOT EXISTS (SELECT FROM signals WHERE runtime = :'runtime' AND role = 'relay') THEN 'no_heartbeat'
            WHEN NOT EXISTS (SELECT FROM signals WHERE runtime = :'runtime' AND role = 'relay' AND fresh)
                THEN 'stale_heartbeat' ELSE 'ok' END AS relay
\gset
\endif
\if :has_work
SELECT CASE WHEN :'worker' = 'ok' AND EXISTS (
    SELECT FROM public.assozeta_handoff_work WHERE owner_runtime = :'runtime' AND state IN ('pending','held','staged')
    AND coalesce(eta, accepted_at) < now() - make_interval(secs => :'stall'::double precision)
) THEN 'stalled' ELSE :'worker' END AS worker
\gset
\endif
\if :has_scheduler
SELECT coalesce((SELECT CASE WHEN runtime <> :'runtime' AND lease_until > now() THEN 'inactive_runtime_active'
                             WHEN runtime <> :'runtime' THEN 'no_lease'
                             WHEN lease_until <= now() OR heartbeat_at NOT BETWEEN
                                 now() - make_interval(secs => :'heartbeat'::double precision) AND now()
                                 THEN 'stale_lease' ELSE 'ok' END
                 FROM public.assozeta_scheduler_lease WHERE singleton = 1), 'no_lease') AS scheduler
\gset
\endif
\if :has_events
SELECT CASE WHEN :'relay' = 'ok' AND EXISTS (
    SELECT FROM public.assozeta_go_realtime_events WHERE delivered_at IS NULL AND failed_code IS NULL
    AND created_at < now() - make_interval(secs => :'stall'::double precision)
) THEN 'stalled' ELSE :'relay' END AS relay
\gset
\endif
SELECT :'runtime', :'generation', :'schema', :'worker', :'scheduler', :'relay';
COMMIT;
