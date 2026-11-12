DO $$ BEGIN
    RAISE EXCEPTION 'Periodic publication receipts and durable intent must survive binary rollback; down migration is unsupported';
END $$;
