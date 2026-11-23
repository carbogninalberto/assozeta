ALTER TABLE public.assozeta_go_job_ownership
    ADD COLUMN operation_id uuid,
    ADD COLUMN committed_at timestamptz;

CREATE FUNCTION public.assozeta_commit_runtime_authority(
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
    ELSIF owner.runtime = p_expected_runtime AND owner.generation = p_expected_generation THEN
        UPDATE public.assozeta_go_job_ownership AS o
           SET runtime = p_target, generation = o.generation + 1,
               operation_id = p_operation_id, committed_at = clock_timestamp()
         WHERE singleton = 1 RETURNING * INTO owner;
        outcome := 'committed';
    ELSE
        outcome := 'conflict';
    END IF;
    RETURN QUERY SELECT outcome, owner.runtime, owner.generation, owner.operation_id;
END $$;
