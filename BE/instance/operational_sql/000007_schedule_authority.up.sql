CREATE TABLE public.assozeta_periodic_seed (
    name text PRIMARY KEY,
    task text NOT NULL,
    definition jsonb NOT NULL,
    definition_sha256 text NOT NULL,
    seeded_at timestamptz NOT NULL,
    seeded_by text NOT NULL CHECK (seeded_by IN ('django','go','migration'))
);

CREATE TABLE public.assozeta_scheduler_lease (
    singleton smallint PRIMARY KEY CHECK (singleton=1),
    runtime text NOT NULL CHECK (runtime IN ('django','go')),
    holder text NOT NULL,
    scheduler_generation bigint NOT NULL CHECK (scheduler_generation > 0),
    ownership_generation bigint NOT NULL,
    acquired_at timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    lease_until timestamptz NOT NULL
);

ALTER TABLE public.assozeta_go_periodic_occurrences
    ALTER COLUMN job_id DROP NOT NULL,
    ADD COLUMN runtime text NOT NULL DEFAULT 'go' CHECK (runtime IN ('django','go')),
    ADD COLUMN task_id text UNIQUE,
    ADD COLUMN scheduler_generation bigint,
    ADD COLUMN broker_pushed_at timestamptz;
UPDATE public.assozeta_go_periodic_occurrences SET task_id=job_id::text;
ALTER TABLE public.assozeta_go_periodic_occurrences ALTER COLUMN task_id SET NOT NULL;
CREATE INDEX assozeta_periodic_pending_push ON public.assozeta_go_periodic_occurrences (runtime,task_id)
    WHERE job_id IS NULL AND broker_pushed_at IS NULL;

ALTER TABLE public.assozeta_handoff_consumers DROP CONSTRAINT assozeta_handoff_consumers_role_check,
    ADD CHECK (role IN ('broker','durable','scheduler','outbox','relay'));

CREATE FUNCTION public.assozeta_scheduler_acquire(p_runtime text, p_holder text, p_lease_seconds double precision)
RETURNS TABLE(outcome text, scheduler_generation bigint) LANGUAGE plpgsql AS $$
DECLARE
    o public.assozeta_go_job_ownership%ROWTYPE;
    l public.assozeta_scheduler_lease%ROWTYPE;
    stamp timestamptz;
BEGIN
    SELECT * INTO STRICT o FROM public.assozeta_go_job_ownership WHERE singleton=1 FOR SHARE;
    IF o.runtime IS DISTINCT FROM p_runtime THEN outcome := 'not_owner'; RETURN NEXT; RETURN; END IF;
    IF o.admission <> 'open' THEN outcome := 'frozen'; RETURN NEXT; RETURN; END IF;
    IF p_lease_seconds <= 0 OR p_holder = '' THEN RAISE EXCEPTION 'invalid scheduler lease'; END IF;
    stamp := clock_timestamp();
    INSERT INTO public.assozeta_scheduler_lease VALUES (1,p_runtime,p_holder,1,o.generation,stamp,stamp,
        stamp + make_interval(secs => p_lease_seconds)) ON CONFLICT DO NOTHING;
    IF FOUND THEN outcome := 'acquired'; scheduler_generation := 1; RETURN NEXT; RETURN; END IF;
    SELECT * INTO STRICT l FROM public.assozeta_scheduler_lease WHERE singleton=1 FOR UPDATE;
    stamp := clock_timestamp();
    scheduler_generation := l.scheduler_generation;
    IF l.ownership_generation = o.generation AND l.runtime = o.runtime AND l.lease_until > stamp THEN
        IF l.holder <> p_holder THEN outcome := 'held'; RETURN NEXT; RETURN; END IF;
        outcome := 'renewed';
    ELSE
        outcome := 'acquired';
        scheduler_generation := l.scheduler_generation + 1;
    END IF;
    UPDATE public.assozeta_scheduler_lease SET runtime=p_runtime,holder=p_holder,
        ownership_generation=o.generation,scheduler_generation=assozeta_scheduler_acquire.scheduler_generation,
        acquired_at=CASE WHEN outcome='renewed' THEN l.acquired_at ELSE stamp END,
        heartbeat_at=stamp,lease_until=stamp + make_interval(secs => p_lease_seconds)
    WHERE singleton=1;
    RETURN NEXT;
END $$;

CREATE FUNCTION public.assozeta_scheduler_fence(p_holder text,p_scheduler_generation bigint)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE o public.assozeta_go_job_ownership%ROWTYPE;
BEGIN
    SELECT * INTO STRICT o FROM public.assozeta_go_job_ownership WHERE singleton=1 FOR SHARE;
    PERFORM 1 FROM public.assozeta_scheduler_lease WHERE singleton=1 AND holder=p_holder
        AND scheduler_generation=p_scheduler_generation AND lease_until>clock_timestamp()
        AND runtime=o.runtime AND ownership_generation=o.generation AND o.admission='open' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'scheduler fenced' USING ERRCODE='AZF02'; END IF;
    RETURN o.generation;
END $$;

CREATE FUNCTION public.assozeta_seed_periodic_definitions(p_runtime text,p_definitions jsonb)
RETURNS TABLE(name text,outcome text) LANGUAGE plpgsql AS $$
#variable_conflict use_column
DECLARE
    d jsonb;
    s jsonb;
    opts jsonb;
    old jsonb;
    row_ record;
    changed boolean := false;
    interval_id_ integer;
    cron_id integer;
    stamp timestamptz := COALESCE(nullif(current_setting('assozeta.scheduler_now',true),'')::timestamptz,clock_timestamp());
BEGIN
    PERFORM 1 FROM public.assozeta_go_job_ownership WHERE singleton=1 FOR SHARE;
    -- Serialize even the first seed, when neither the definition nor its receipt exists.
    LOCK TABLE public.assozeta_periodic_seed IN SHARE ROW EXCLUSIVE MODE;
    FOR d IN SELECT value FROM jsonb_array_elements(p_definitions) LOOP
        name := d->>'name';
        s := d->'schedule';
        opts := d->'options';
        SELECT seed.definition INTO old FROM public.assozeta_periodic_seed seed WHERE seed.name=d->>'name';
        SELECT p.* INTO row_ FROM public.django_celery_beat_periodictask p WHERE p.name=d->>'name' FOR UPDATE;
        IF old IS NOT NULL AND row_.id IS NULL THEN outcome := 'kept_deleted';
        ELSIF old=d THEN outcome := 'kept';
        ELSIF old IS NULL THEN outcome := CASE WHEN row_.id IS NULL THEN 'created' ELSE 'adopted' END;
        ELSE
            SELECT CASE WHEN row_.task=old->>'task' AND row_.args::jsonb=old->'args' AND row_.kwargs::jsonb=old->'kwargs'
                AND row_.headers::jsonb=old#>'{options,headers}'
                AND row_.queue IS NOT DISTINCT FROM old#>>'{options,queue}'
                AND row_.exchange IS NOT DISTINCT FROM old#>>'{options,exchange}'
                AND row_.routing_key IS NOT DISTINCT FROM old#>>'{options,routing_key}'
                AND row_.priority IS NOT DISTINCT FROM (old#>>'{options,priority}')::int
                AND row_.expire_seconds IS NOT DISTINCT FROM (old#>>'{options,expire_seconds}')::int
                AND row_.expires IS NULL AND (
                    EXISTS (SELECT 1 FROM public.django_celery_beat_intervalschedule i WHERE i.id=row_.interval_id
                        AND old->'schedule'=jsonb_build_object('kind','interval','every',i.every,'period',i.period))
                    OR EXISTS (SELECT 1 FROM public.django_celery_beat_crontabschedule c WHERE c.id=row_.crontab_id
                        AND old->'schedule'=((to_jsonb(c)-'id') || '{"kind":"crontab"}'::jsonb)))
                THEN 'updated' ELSE 'kept_custom' END INTO outcome;
        END IF;
        IF outcome IN ('created','adopted','updated') THEN
            cron_id := NULL; interval_id_ := NULL;
            IF s->>'kind' = 'interval' THEN
                SELECT id INTO interval_id_ FROM public.django_celery_beat_intervalschedule
                    WHERE every=(s->>'every')::int AND period=s->>'period' ORDER BY id LIMIT 1;
                IF NOT FOUND THEN INSERT INTO public.django_celery_beat_intervalschedule(every,period)
                    VALUES ((s->>'every')::int,s->>'period') RETURNING id INTO interval_id_; END IF;
            ELSIF s->>'kind' = 'crontab' THEN
                SELECT id INTO cron_id FROM public.django_celery_beat_crontabschedule c
                    WHERE to_jsonb(c)-'id'=s-'kind' ORDER BY id LIMIT 1;
                IF NOT FOUND THEN INSERT INTO public.django_celery_beat_crontabschedule
                    (minute,hour,day_of_week,day_of_month,month_of_year,timezone) VALUES
                    (s->>'minute',s->>'hour',s->>'day_of_week',s->>'day_of_month',s->>'month_of_year',s->>'timezone')
                    RETURNING id INTO cron_id; END IF;
            ELSE RAISE EXCEPTION 'unsupported declared schedule';
            END IF;
            INSERT INTO public.django_celery_beat_periodictask
                (name,task,interval_id,crontab_id,args,kwargs,queue,exchange,routing_key,priority,headers,
                 expire_seconds,date_changed,enabled,one_off,total_run_count,description)
            VALUES (d->>'name',d->>'task',interval_id_,cron_id,d->>'args',d->>'kwargs',opts->>'queue',
                opts->>'exchange',opts->>'routing_key',(opts->>'priority')::int,opts->>'headers',
                (opts->>'expire_seconds')::int,stamp,true,false,0,'')
            ON CONFLICT (name) DO UPDATE SET task=excluded.task,interval_id=excluded.interval_id,crontab_id=excluded.crontab_id,
                solar_id=NULL,clocked_id=NULL,args=excluded.args,kwargs=excluded.kwargs,queue=excluded.queue,
                exchange=excluded.exchange,routing_key=excluded.routing_key,priority=excluded.priority,headers=excluded.headers,
                expire_seconds=excluded.expire_seconds,date_changed=excluded.date_changed;
            changed := true;
        END IF;
        IF old IS DISTINCT FROM d THEN
            INSERT INTO public.assozeta_periodic_seed VALUES
                (name,d->>'task',d,encode(sha256(convert_to(d::text,'UTF8')),'hex'),clock_timestamp(),p_runtime)
            ON CONFLICT (name) DO UPDATE SET task=excluded.task,definition=excluded.definition,
                definition_sha256=excluded.definition_sha256,seeded_at=excluded.seeded_at,seeded_by=excluded.seeded_by;
        END IF;
        RETURN NEXT;
    END LOOP;
    IF changed THEN
        INSERT INTO public.django_celery_beat_periodictasks(ident,last_update) VALUES (1,clock_timestamp())
        ON CONFLICT (ident) DO UPDATE SET last_update=excluded.last_update;
    END IF;
END $$;

-- Hash the same persisted definition on both runtimes. Cursor-only changes and
-- surrogate IDs do not create a new definition.
CREATE FUNCTION public.assozeta_periodic_digest(p_id integer) RETURNS text
LANGUAGE plpgsql STABLE SET timezone = 'UTC' AS $$
DECLARE digest text;
BEGIN
    SELECT encode(sha256(convert_to(jsonb_build_array(
        to_jsonb(p) - ARRAY['id','interval_id','crontab_id','clocked_id','solar_id','date_changed','last_run_at','total_run_count'],
        to_jsonb(i)-'id',to_jsonb(c)-'id',to_jsonb(k)-'id',to_jsonb(s)-'id')::text, 'UTF8')), 'hex')
    INTO STRICT digest FROM public.django_celery_beat_periodictask p
    LEFT JOIN public.django_celery_beat_intervalschedule i ON i.id=p.interval_id
    LEFT JOIN public.django_celery_beat_crontabschedule c ON c.id=p.crontab_id
    LEFT JOIN public.django_celery_beat_clockedschedule k ON k.id=p.clocked_id
    LEFT JOIN public.django_celery_beat_solarschedule s ON s.id=p.solar_id WHERE p.id=p_id;
    RETURN digest;
END $$;

-- On a graceful exit, retire this holder immediately. Keep its generation
-- counter for the next process, and acknowledge any concurrent close.
CREATE FUNCTION public.assozeta_scheduler_release(p_runtime text,p_holder text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE o public.assozeta_go_job_ownership%ROWTYPE;
BEGIN
    SELECT * INTO o FROM public.assozeta_go_job_ownership WHERE singleton=1 FOR SHARE;
    UPDATE public.assozeta_scheduler_lease SET lease_until=clock_timestamp()
        WHERE singleton=1 AND runtime=p_runtime AND holder=p_holder;
    IF o.runtime=p_runtime AND o.admission='frozen' THEN
        PERFORM public.assozeta_handoff_acknowledge_freeze(p_holder,o.admission_operation_id);
    ELSE
        DELETE FROM public.assozeta_handoff_consumers WHERE consumer_id=p_holder AND role='scheduler';
    END IF;
END $$;
