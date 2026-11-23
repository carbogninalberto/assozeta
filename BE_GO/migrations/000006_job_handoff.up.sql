-- A job keeps its identity across deliveries, attempts and runtime generations.
ALTER TABLE public.assozeta_go_job_ownership
    ADD COLUMN admission text NOT NULL DEFAULT 'open' CHECK (admission IN ('open', 'frozen')),
    ADD COLUMN admission_operation_id uuid,
    ADD CHECK ((admission = 'frozen') = (admission_operation_id IS NOT NULL));

CREATE TABLE public.assozeta_handoff_work (
    task_id text PRIMARY KEY CHECK (octet_length(task_id) BETWEEN 1 AND 255),
    task_name text NOT NULL CHECK (octet_length(task_name) BETWEEN 1 AND 255),
    transport text NOT NULL CHECK (transport IN ('broker', 'sql')),
    queue text,
    envelope bytea,
    origin_runtime text NOT NULL CHECK (origin_runtime IN ('django', 'go')),
    owner_runtime text CHECK (owner_runtime IN ('django', 'go')),
    owner_generation bigint,
    operation_id uuid,
    state text NOT NULL CHECK (state IN ('pending', 'held', 'staged', 'running', 'completed', 'failed', 'cancelled',
                                         'expired', 'revoked', 'blocked')),
    accepted_at timestamptz NOT NULL,
    eta timestamptz,
    expires timestamptz,
    parent_id text,
    root_id text,
    retries integer NOT NULL DEFAULT 0,
    priority smallint,
    source_position bigint,
    association_id uuid,
    block_reason text,
    resolved_at timestamptz,
    resolved_by text,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX assozeta_handoff_work_owner ON public.assozeta_handoff_work (owner_runtime, owner_generation, state);
CREATE INDEX assozeta_handoff_work_operation ON public.assozeta_handoff_work (operation_id) WHERE operation_id IS NOT NULL;

CREATE TABLE public.assozeta_handoff_attempts (
    attempt_id uuid PRIMARY KEY,
    task_id text NOT NULL REFERENCES public.assozeta_handoff_work (task_id),
    runtime text NOT NULL CHECK (runtime IN ('django', 'go')),
    generation bigint NOT NULL,
    consumer_id text NOT NULL,
    retries integer NOT NULL DEFAULT 0,
    delivery_tag text,
    lease_until timestamptz,
    claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    outcome text NOT NULL DEFAULT 'running' CHECK (outcome IN ('running', 'completed', 'failed', 'retried', 'fenced', 'released'))
);
CREATE INDEX assozeta_handoff_attempts_task ON public.assozeta_handoff_attempts (task_id);

CREATE TABLE public.assozeta_handoff_effects (
    effect_key text PRIMARY KEY,
    task_id text NOT NULL REFERENCES public.assozeta_handoff_work,
    attempt_id uuid REFERENCES public.assozeta_handoff_attempts,
    kind text NOT NULL,
    state text NOT NULL CHECK (state IN ('intent','confirmed','ambiguous','reconciled_sent','reconciled_unsent')),
    provider_ref text,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolved_at timestamptz,
    resolved_by text
);
CREATE INDEX assozeta_handoff_effects_attempt ON public.assozeta_handoff_effects(attempt_id);

CREATE TABLE public.assozeta_handoff_transfers (
    operation_id uuid NOT NULL,
    task_id text NOT NULL REFERENCES public.assozeta_handoff_work (task_id),
    source_runtime text NOT NULL,
    source_generation bigint NOT NULL,
    target_runtime text NOT NULL,
    target_generation bigint,
    source_location text NOT NULL CHECK (source_location IN ('ledger', 'queue', 'unacked', 'sql')),
    staged_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    source_removed_at timestamptz,
    delivered_at timestamptz,
    delivery text,
    PRIMARY KEY (operation_id, task_id)
);

CREATE TABLE public.assozeta_handoff_consumers (
    consumer_id text PRIMARY KEY CHECK (octet_length(consumer_id) BETWEEN 1 AND 255),
    runtime text NOT NULL CHECK (runtime IN ('django', 'go')),
    role text NOT NULL CHECK (role IN ('broker', 'durable')),
    registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    heartbeat_seq bigint NOT NULL DEFAULT 1,
    freeze_operation_id uuid,
    frozen_at timestamptz
);

CREATE FUNCTION public.assozeta_handoff_admit(p_runtime text, p_task_id text, p_task_name text, p_transport text,
    p_queue text, p_envelope bytea, p_accepted_at timestamptz, p_eta timestamptz, p_expires timestamptz)
RETURNS TABLE(outcome text, generation bigint) LANGUAGE plpgsql AS $$
#variable_conflict use_column
DECLARE
    o public.assozeta_go_job_ownership%ROWTYPE;
    message jsonb;
    open_ boolean;
BEGIN
    SELECT * INTO STRICT o FROM public.assozeta_go_job_ownership WHERE singleton = 1 FOR SHARE;
    generation := o.generation;
    IF p_runtime IS DISTINCT FROM o.runtime THEN
        outcome := 'rejected'; RETURN NEXT; RETURN;
    END IF;
    IF p_envelope IS NOT NULL THEN
        BEGIN
            message := convert_from(p_envelope, 'UTF8')::jsonb;
        EXCEPTION WHEN others THEN
            message := NULL;
        END;
    END IF;
    open_ := o.admission = 'open';
    INSERT INTO public.assozeta_handoff_work AS w (task_id, task_name, transport, queue, envelope, origin_runtime,
        owner_runtime, owner_generation, operation_id, state, accepted_at, eta, expires, parent_id, root_id, retries, priority)
    VALUES (p_task_id, p_task_name, p_transport, p_queue, p_envelope, p_runtime,
        CASE WHEN open_ THEN p_runtime END, CASE WHEN open_ THEN o.generation END,
        CASE WHEN open_ THEN NULL ELSE o.admission_operation_id END, CASE WHEN open_ THEN 'pending' ELSE 'held' END,
        COALESCE(p_accepted_at, 'epoch'::timestamptz), p_eta, p_expires, message->'headers'->>'parent_id',
        message->'headers'->>'root_id', COALESCE((message->'headers'->>'retries')::integer, 0),
        (message->'properties'->>'priority')::smallint)
    ON CONFLICT (task_id) DO UPDATE SET
        queue = excluded.queue, envelope = excluded.envelope, eta = excluded.eta, expires = excluded.expires,
        parent_id = excluded.parent_id, root_id = excluded.root_id, retries = excluded.retries, priority = excluded.priority,
        owner_runtime = excluded.owner_runtime, owner_generation = excluded.owner_generation,
        operation_id = CASE WHEN open_ THEN w.operation_id ELSE excluded.operation_id END,
        state = CASE WHEN open_ THEN w.state ELSE 'held' END, updated_at = clock_timestamp()
    WHERE w.transport = 'broker' AND excluded.transport = 'broker'
      AND w.task_name = excluded.task_name AND w.state IN ('pending', 'running')
      AND w.owner_runtime = p_runtime AND w.owner_generation = o.generation AND excluded.retries >= w.retries;
    IF NOT FOUND THEN
        outcome := 'duplicate'; RETURN NEXT; RETURN;
    END IF;
    outcome := CASE WHEN open_ THEN 'accepted' ELSE 'held' END;
    RETURN NEXT;
END $$;

CREATE FUNCTION public.assozeta_handoff_claim(p_runtime text, p_consumer_id text, p_task_id text, p_task_name text,
    p_envelope bytea, p_delivery_tag text, p_redelivered boolean, p_execution_id uuid)
RETURNS TABLE(outcome text, attempt_id uuid, generation bigint) LANGUAGE plpgsql AS $$
#variable_conflict use_column
DECLARE
    o public.assozeta_go_job_ownership%ROWTYPE;
    w public.assozeta_handoff_work%ROWTYPE;
    previous uuid;
    stamp timestamptz;
    message jsonb;
BEGIN
    p_delivery_tag := NULLIF(p_delivery_tag,'');
    SELECT * INTO STRICT o FROM public.assozeta_go_job_ownership WHERE singleton = 1 FOR SHARE;
    generation := o.generation;
    IF p_runtime IS DISTINCT FROM o.runtime THEN
        outcome := 'not_owner'; RETURN NEXT; RETURN;
    END IF;
    SELECT * INTO w FROM public.assozeta_handoff_work WHERE task_id = p_task_id FOR UPDATE;
    IF NOT FOUND THEN
        IF o.admission = 'frozen' THEN
            outcome := 'frozen'; RETURN NEXT; RETURN;
        END IF;
        BEGIN
            message := convert_from(p_envelope, 'UTF8')::jsonb;
            stamp := to_timestamp((message->'headers'->>'assozeta_published_at')::double precision);
        EXCEPTION WHEN others THEN
            stamp := NULL;
        END;
        INSERT INTO public.assozeta_handoff_work (task_id, task_name, transport, queue, envelope, origin_runtime,
            owner_runtime, owner_generation, state, accepted_at, eta, expires, retries, priority, parent_id, root_id)
        VALUES (p_task_id, p_task_name, 'broker', message#>>'{properties,delivery_info,routing_key}', p_envelope,
            p_runtime, p_runtime, o.generation, 'pending', COALESCE(stamp, 'epoch'::timestamptz),
            (message#>>'{headers,eta}')::timestamptz, (message#>>'{headers,expires}')::timestamptz,
            coalesce((message#>>'{headers,retries}')::integer,0), (message#>>'{properties,priority}')::smallint,
            message#>>'{headers,parent_id}', message#>>'{headers,root_id}')
        ON CONFLICT (task_id) DO NOTHING;
        SELECT * INTO STRICT w FROM public.assozeta_handoff_work WHERE task_id = p_task_id FOR UPDATE;
    END IF;
    -- The ownership and work row locks serialize competing recoveries. A
    -- fenced transaction may hold the attempt row through its business effect;
    -- do not wait on it while holding the work row it needs to finish.
    IF p_redelivered AND w.state = 'running' AND w.task_name = p_task_name
       AND o.admission = 'open' AND w.owner_runtime = p_runtime AND w.owner_generation = o.generation THEN
        SELECT a.attempt_id INTO previous FROM public.assozeta_handoff_attempts a
            WHERE a.task_id=p_task_id AND a.runtime=p_runtime AND a.generation=o.generation
                AND a.outcome='running' AND a.delivery_tag=p_delivery_tag;
        IF previous IS NOT NULL THEN
            PERFORM 1 FROM public.assozeta_handoff_attempts a WHERE a.attempt_id=previous
                AND a.outcome='running' AND a.lease_until<=clock_timestamp() FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN
                outcome := 'active'; RETURN NEXT; RETURN;
            END IF;
            PERFORM public.assozeta_handoff_finish(previous,'released');
            SELECT * INTO STRICT w FROM public.assozeta_handoff_work WHERE task_id=p_task_id;
        END IF;
    END IF;
    IF w.state = 'blocked' THEN
        outcome := 'blocked'; RETURN NEXT; RETURN;
    END IF;
    IF w.state IN ('running', 'completed', 'failed', 'cancelled', 'expired', 'revoked') THEN
        outcome := 'duplicate'; RETURN NEXT; RETURN;
    END IF;
    IF o.admission = 'frozen' THEN
        outcome := 'frozen'; RETURN NEXT; RETURN;
    END IF;
    IF w.task_name IS DISTINCT FROM p_task_name OR w.state <> 'pending' OR w.owner_runtime IS DISTINCT FROM p_runtime OR w.owner_generation IS DISTINCT FROM o.generation THEN
        outcome := 'not_owner'; RETURN NEXT; RETURN;
    END IF;
    -- A retry/deferral admits a new envelope with the same logical task ID.
    IF p_delivery_tag IS NOT NULL AND w.envelope IS NOT NULL AND
       convert_from(w.envelope,'UTF8')::jsonb #>> '{properties,delivery_tag}' <> p_delivery_tag THEN
        outcome := 'duplicate'; RETURN NEXT; RETURN;
    END IF;
    IF w.expires <= clock_timestamp() THEN
        UPDATE public.assozeta_handoff_work SET state='expired',updated_at=clock_timestamp() WHERE task_id=p_task_id;
        outcome := 'expired'; RETURN NEXT; RETURN;
    END IF;
    attempt_id := coalesce(p_execution_id, gen_random_uuid());
    INSERT INTO public.assozeta_handoff_attempts (attempt_id, task_id, runtime, generation, consumer_id, retries, delivery_tag, lease_until)
    VALUES (attempt_id, p_task_id, p_runtime, o.generation, p_consumer_id, w.retries, p_delivery_tag,
            CASE WHEN p_delivery_tag IS NOT NULL THEN clock_timestamp() + interval '30 seconds' END);
    UPDATE public.assozeta_handoff_work SET state = 'running', updated_at = clock_timestamp() WHERE task_id = p_task_id;
    outcome := 'claimed';
    RETURN NEXT;
END $$;

-- Consumers without a locally assigned execution id use a database UUID.
CREATE FUNCTION public.assozeta_handoff_claim(p_runtime text, p_consumer_id text, p_task_id text, p_task_name text,
    p_envelope bytea, p_delivery_tag text, p_redelivered boolean)
RETURNS TABLE(outcome text, attempt_id uuid, generation bigint) LANGUAGE sql AS $$
    SELECT * FROM public.assozeta_handoff_claim($1,$2,$3,$4,$5,$6,$7,NULL)
$$;

-- Durable jobs use their lease, and keep the original claim interface.
CREATE FUNCTION public.assozeta_handoff_claim(p_runtime text, p_consumer_id text, p_task_id text, p_task_name text,
    p_envelope bytea)
RETURNS TABLE(outcome text, attempt_id uuid, generation bigint) LANGUAGE sql AS $$
    SELECT * FROM public.assozeta_handoff_claim($1,$2,$3,$4,$5,NULL,false)
$$;

-- Only the current execution can renew. Skip an attempt locked by its own
-- fenced effect: a racing recovery must skip the same lock.
CREATE FUNCTION public.assozeta_handoff_renew(p_attempts uuid[]) RETURNS void LANGUAGE sql AS $$
    WITH renewable AS (
        SELECT a.attempt_id FROM public.assozeta_handoff_attempts a
          JOIN public.assozeta_go_job_ownership o ON o.singleton=1 AND o.runtime=a.runtime AND o.generation=a.generation
         WHERE a.attempt_id=ANY(p_attempts) AND a.outcome='running' AND a.lease_until>clock_timestamp()
         FOR SHARE OF o FOR UPDATE OF a SKIP LOCKED
    )
    UPDATE public.assozeta_handoff_attempts a SET lease_until=clock_timestamp() + interval '30 seconds'
      FROM renewable r WHERE a.attempt_id=r.attempt_id
$$;

CREATE FUNCTION public.assozeta_handoff_fence(p_attempt_id uuid) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    o public.assozeta_go_job_ownership%ROWTYPE;
    found_generation bigint;
BEGIN
    SELECT * INTO STRICT o FROM public.assozeta_go_job_ownership WHERE singleton = 1 FOR SHARE;
    SELECT a.generation INTO found_generation FROM public.assozeta_handoff_attempts a
     WHERE a.attempt_id = p_attempt_id AND a.outcome = 'running' AND a.runtime = o.runtime AND a.generation = o.generation FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'handoff attempt fenced' USING ERRCODE = 'AZF01';
    END IF;
    RETURN found_generation;
END $$;

CREATE FUNCTION public.assozeta_handoff_effect_intent(p_attempt_id uuid,p_effect_key text,p_kind text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    task text;
    existing text;
BEGIN
    PERFORM public.assozeta_handoff_fence(p_attempt_id);
    SELECT task_id INTO STRICT task FROM public.assozeta_handoff_attempts WHERE attempt_id=p_attempt_id;
    INSERT INTO public.assozeta_handoff_effects(effect_key,task_id,attempt_id,kind,state)
        VALUES(p_effect_key,task,p_attempt_id,p_kind,'intent') ON CONFLICT DO NOTHING;
    IF FOUND THEN RETURN 'recorded'; END IF;
    SELECT state INTO existing FROM public.assozeta_handoff_effects WHERE effect_key=p_effect_key AND task_id=task;
    RETURN CASE WHEN existing IN ('confirmed','reconciled_sent') THEN 'confirmed' ELSE 'blocked' END;
END $$;

CREATE FUNCTION public.assozeta_handoff_effect_confirm(p_effect_key text,p_provider_ref text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    attempt uuid;
BEGIN
    SELECT attempt_id INTO STRICT attempt FROM public.assozeta_handoff_effects WHERE effect_key=p_effect_key;
    PERFORM public.assozeta_handoff_fence(attempt);
    UPDATE public.assozeta_handoff_effects SET state='confirmed',provider_ref=p_provider_ref
        WHERE effect_key=p_effect_key AND state IN ('intent','confirmed');
    IF NOT FOUND THEN RAISE EXCEPTION 'effect is blocked' USING ERRCODE='AZF01'; END IF;
    RETURN 'confirmed';
END $$;

CREATE FUNCTION public.assozeta_handoff_finish(p_attempt_id uuid, p_outcome text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    task text;
BEGIN
    IF p_outcome IS NULL OR p_outcome NOT IN ('completed', 'failed', 'retried', 'released') THEN
        RAISE EXCEPTION 'Invalid attempt outcome' USING ERRCODE = '22023';
    END IF;
    PERFORM public.assozeta_handoff_fence(p_attempt_id);
    UPDATE public.assozeta_handoff_attempts SET outcome = p_outcome, finished_at = clock_timestamp()
     WHERE attempt_id = p_attempt_id RETURNING task_id INTO task;
    UPDATE public.assozeta_handoff_work
       SET state = CASE p_outcome WHEN 'completed' THEN 'completed' WHEN 'failed' THEN 'failed' ELSE 'pending' END,
           retries = retries + CASE WHEN p_outcome = 'retried' AND transport = 'sql' THEN 1 ELSE 0 END,
           updated_at = clock_timestamp()
     WHERE task_id = task AND (p_outcome <> 'retried' OR state = 'running');
    UPDATE public.assozeta_handoff_effects SET state='ambiguous' WHERE attempt_id=p_attempt_id AND state='intent';
    IF FOUND THEN
        UPDATE public.assozeta_handoff_work SET state='blocked',block_reason='ambiguous_effect' WHERE task_id=task;
    END IF;
    RETURN p_outcome;
END $$;

CREATE FUNCTION public.assozeta_handoff_record_fenced(p_attempt_id uuid) RETURNS text LANGUAGE sql AS $$
    UPDATE public.assozeta_handoff_attempts SET outcome = 'fenced', finished_at = clock_timestamp()
     WHERE attempt_id = p_attempt_id AND outcome = 'running' RETURNING 'fenced'::text
$$;

CREATE FUNCTION public.assozeta_handoff_heartbeat(p_consumer_id text, p_runtime text, p_role text)
RETURNS TABLE(runtime text, generation bigint, admission text, operation_id uuid) LANGUAGE plpgsql AS $$
#variable_conflict use_column
BEGIN
    RETURN QUERY SELECT o.runtime, o.generation, o.admission, o.admission_operation_id
      FROM public.assozeta_go_job_ownership o WHERE o.singleton = 1 FOR SHARE;
    INSERT INTO public.assozeta_handoff_consumers AS c (consumer_id, runtime, role) VALUES (p_consumer_id, p_runtime, p_role)
    ON CONFLICT (consumer_id) DO UPDATE SET heartbeat_at = clock_timestamp(), heartbeat_seq = c.heartbeat_seq + 1;
END $$;

CREATE FUNCTION public.assozeta_handoff_acknowledge_freeze(p_consumer_id text, p_operation_id uuid) RETURNS text
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM public.assozeta_go_job_ownership o JOIN public.assozeta_handoff_consumers c
      ON o.runtime = c.runtime WHERE o.singleton = 1 AND c.consumer_id = p_consumer_id
      AND o.admission_operation_id = p_operation_id FOR SHARE OF o;
    IF NOT FOUND THEN RETURN 'conflict'; END IF;
    UPDATE public.assozeta_handoff_consumers SET frozen_at = CASE WHEN freeze_operation_id = p_operation_id THEN frozen_at ELSE clock_timestamp() END,
           freeze_operation_id = p_operation_id
     WHERE consumer_id = p_consumer_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Unknown consumer' USING ERRCODE = '22023';
    END IF;
    RETURN 'acknowledged';
END $$;

CREATE FUNCTION public.assozeta_handoff_close_admission(p_operation_id uuid, p_runtime text, p_generation bigint)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    o public.assozeta_go_job_ownership%ROWTYPE;
BEGIN
    SELECT * INTO STRICT o FROM public.assozeta_go_job_ownership WHERE singleton = 1 FOR UPDATE;
    IF o.runtime IS DISTINCT FROM p_runtime OR o.generation IS DISTINCT FROM p_generation THEN
        RETURN 'conflict';
    END IF;
    IF o.admission = 'frozen' THEN
        RETURN CASE WHEN o.admission_operation_id = p_operation_id THEN 'already_closed' ELSE 'conflict' END;
    END IF;
    UPDATE public.assozeta_go_job_ownership SET admission = 'frozen', admission_operation_id = p_operation_id WHERE singleton = 1;
    RETURN 'closed';
END $$;

CREATE OR REPLACE FUNCTION public.assozeta_commit_runtime_authority(
    p_operation_id uuid, p_expected_runtime text, p_expected_generation bigint, p_target text
) RETURNS TABLE(outcome text, runtime text, generation bigint, operation_id uuid)
LANGUAGE plpgsql AS $$
DECLARE
    owner public.assozeta_go_job_ownership%ROWTYPE;
BEGIN
    IF p_operation_id IS NULL OR p_target IS NULL OR p_target NOT IN ('django', 'go')
       OR p_target = p_expected_runtime THEN
        RAISE EXCEPTION 'Invalid runtime transition' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO STRICT owner FROM public.assozeta_go_job_ownership WHERE singleton = 1 FOR UPDATE;
    IF owner.operation_id = p_operation_id THEN
        outcome := 'already_committed';
    ELSIF owner.runtime = p_expected_runtime AND owner.generation = p_expected_generation
          AND (owner.admission = 'open' OR owner.admission_operation_id = p_operation_id) THEN
        UPDATE public.assozeta_go_job_ownership AS o
           SET runtime = p_target, generation = o.generation + 1,
               operation_id = p_operation_id, committed_at = clock_timestamp(),
               admission = 'open', admission_operation_id = NULL
         WHERE singleton = 1 RETURNING * INTO owner;
        -- Held arrivals can commit after the last staging batch. Enumerate them
        -- under the exclusive authority lock so activation sees every receipt.
        INSERT INTO public.assozeta_handoff_transfers (operation_id, task_id, source_runtime,
            source_generation, target_runtime, source_location)
        SELECT p_operation_id, w.task_id, p_expected_runtime, p_expected_generation, p_target,
               CASE w.transport WHEN 'sql' THEN 'sql' ELSE 'ledger' END
          FROM public.assozeta_handoff_work w WHERE w.operation_id = p_operation_id AND w.state = 'held';
        UPDATE public.assozeta_handoff_work AS w
           SET state = 'pending', owner_runtime = p_target, owner_generation = owner.generation, updated_at = clock_timestamp()
         WHERE w.operation_id = p_operation_id AND w.state IN ('held', 'staged');
        UPDATE public.assozeta_handoff_transfers AS t SET target_generation = owner.generation
         WHERE t.operation_id = p_operation_id;
        outcome := 'committed';
    ELSE
        outcome := 'conflict';
    END IF;
    RETURN QUERY SELECT outcome, owner.runtime, owner.generation, owner.operation_id;
END $$;
