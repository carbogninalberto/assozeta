DO $$ BEGIN
    RAISE EXCEPTION 'Accepted jobs and transfer receipts must survive binary rollback; down migration is unsupported';
END $$;
