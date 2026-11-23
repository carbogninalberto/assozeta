CREATE TABLE public.assozeta_outbox_effects (
    outbox text NOT NULL CHECK (outbox = 'whatsapp'),
    item_id text NOT NULL,
    attempt integer NOT NULL CHECK (attempt > 0),
    runtime text NOT NULL CHECK (runtime IN ('django','go')),
    ownership_generation bigint NOT NULL,
    consumer_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('intent','confirmed','ambiguous','reconciled_sent','reconciled_unsent')),
    provider_ref text,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolved_at timestamptz,
    resolved_by text,
    PRIMARY KEY(outbox,item_id,attempt)
);
CREATE UNIQUE INDEX assozeta_outbox_effect_once ON public.assozeta_outbox_effects(outbox,item_id)
    WHERE state <> 'reconciled_unsent';

ALTER TABLE public.assozeta_go_realtime_events ADD COLUMN delivered_runtime text CHECK (delivered_runtime IN ('django','go')),
    ADD COLUMN delivered_generation bigint;

CREATE FUNCTION public.assozeta_outbox_claim(p_runtime text,p_consumer_id text,p_outbox text)
RETURNS TABLE(outcome text,generation bigint) LANGUAGE sql AS $$
    SELECT CASE WHEN runtime IS DISTINCT FROM p_runtime OR nullif(p_consumer_id,'') IS NULL
            OR p_outbox IS NULL OR p_outbox NOT IN ('whatsapp','realtime') THEN 'not_owner'
        WHEN admission <> 'open' THEN 'frozen' ELSE 'owner' END, o.generation
    FROM public.assozeta_go_job_ownership o WHERE singleton=1 FOR SHARE
$$;

CREATE FUNCTION public.assozeta_outbox_effect_intent(p_runtime text,p_consumer_id text,p_outbox text,p_item_id text,p_attempt integer)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE owner record;
BEGIN
    SELECT * INTO STRICT owner FROM public.assozeta_outbox_claim(p_runtime,p_consumer_id,p_outbox);
    IF owner.outcome <> 'owner' THEN RETURN 'blocked'; END IF;
    INSERT INTO public.assozeta_outbox_effects(outbox,item_id,attempt,runtime,ownership_generation,consumer_id,state)
        VALUES(p_outbox,p_item_id,p_attempt,p_runtime,owner.generation,p_consumer_id,'intent') ON CONFLICT DO NOTHING;
    IF FOUND THEN RETURN 'recorded'; END IF;
    IF EXISTS (SELECT FROM public.assozeta_outbox_effects WHERE outbox=p_outbox AND item_id=p_item_id
               AND state IN ('confirmed','reconciled_sent')) THEN RETURN 'confirmed'; END IF;
    UPDATE public.assozeta_outbox_effects SET state='ambiguous'
        WHERE outbox=p_outbox AND item_id=p_item_id AND state='intent';
    RETURN 'blocked';
END $$;

CREATE FUNCTION public.assozeta_outbox_effect_confirm(p_outbox text,p_item_id text,p_attempt integer,p_provider_ref text)
RETURNS text LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public.assozeta_outbox_effects SET state='confirmed',provider_ref=p_provider_ref,resolved_at=clock_timestamp(),
        resolved_by=consumer_id WHERE outbox=p_outbox AND item_id=p_item_id AND attempt=p_attempt AND state IN ('intent','ambiguous');
    IF FOUND OR EXISTS(SELECT FROM public.assozeta_outbox_effects WHERE outbox=p_outbox AND item_id=p_item_id
        AND attempt=p_attempt AND state='confirmed' AND provider_ref=p_provider_ref) THEN RETURN 'confirmed'; END IF;
    RAISE EXCEPTION 'No matching outbox send intent';
END $$;

CREATE FUNCTION public.assozeta_outbox_reconcile(p_outbox text,p_item_id text,p_decision text,p_actor text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE outcome text; resolved_attempt integer;
BEGIN
    IF p_decision IS NULL OR p_decision NOT IN ('sent','not_sent') OR nullif(p_actor,'') IS NULL THEN
        RAISE EXCEPTION 'Outbox reconciliation requires a decision and actor';
    END IF;
    -- Serialize operator decisions with sends and callbacks. A negative
    -- decision consumes this attempt number, leaving a fresh one for retry.
    PERFORM 1 FROM communications_whatsappdelivery WHERE id=p_item_id::uuid FOR UPDATE;
    outcome := CASE p_decision WHEN 'sent' THEN 'reconciled_sent' ELSE 'reconciled_unsent' END;
    UPDATE public.assozeta_outbox_effects SET state=outcome,resolved_at=clock_timestamp(),resolved_by=p_actor
        WHERE outbox=p_outbox AND item_id=p_item_id AND state IN ('intent','ambiguous') RETURNING attempt INTO resolved_attempt;
    IF NOT FOUND THEN RETURN 'unchanged'; END IF;
    IF p_decision='not_sent' THEN
        UPDATE communications_whatsappdelivery SET attempts=greatest(attempts,resolved_attempt) WHERE id=p_item_id::uuid;
    END IF;
    RETURN outcome;
END $$;

-- Celery declares the poll's relative TTL as options.expires. Beat stores
-- relative TTLs in expire_seconds. Normalize before comparing seed receipts so
-- the existing seed logic upgrades untouched rows and preserves operator edits.
ALTER FUNCTION public.assozeta_seed_periodic_definitions(text,jsonb)
    RENAME TO assozeta_seed_periodic_definitions_v7;

CREATE FUNCTION public.assozeta_seed_periodic_definitions(p_runtime text,p_definitions jsonb)
RETURNS TABLE(name text,outcome text) LANGUAGE sql AS $$
    SELECT * FROM public.assozeta_seed_periodic_definitions_v7(p_runtime, (
        SELECT coalesce(jsonb_agg(CASE
            WHEN d->>'name'='deliver-whatsapp' AND d->>'task'='communications.tasks.deliver_whatsapp'
                AND d#>>'{options,expire_seconds}' IS NULL AND jsonb_typeof(d#>'{options,expires}')='number'
            THEN jsonb_set(d,'{options,expire_seconds}',d#>'{options,expires}')
            ELSE d END ORDER BY ord),'[]'::jsonb)
        FROM jsonb_array_elements(p_definitions) WITH ORDINALITY AS entries(d,ord)
    ))
$$;
