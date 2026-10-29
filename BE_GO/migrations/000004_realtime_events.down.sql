DO $$ BEGIN
    RAISE EXCEPTION 'Pending realtime events must survive binary rollback; down migration is unsupported';
END $$;
