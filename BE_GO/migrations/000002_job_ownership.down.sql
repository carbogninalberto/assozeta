DO $$ BEGIN
    RAISE EXCEPTION 'Go ownership history and durable intent must survive binary rollback; down migration is unsupported';
END $$;
