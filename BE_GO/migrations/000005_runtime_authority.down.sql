DO $$ BEGIN
    RAISE EXCEPTION 'Runtime authority must survive binary rollback; down migration is unsupported';
END $$;
