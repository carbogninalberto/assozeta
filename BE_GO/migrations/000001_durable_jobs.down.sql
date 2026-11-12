-- Operational records must survive binary/route rollback. Never drop queued work.
DO $$ BEGIN
    RAISE EXCEPTION 'Go operational history is additive; retain jobs during rollback';
END $$;
