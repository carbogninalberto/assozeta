DO $$ BEGIN
    RAISE EXCEPTION 'Scheduler generations, seed records and occurrence receipts must survive binary rollback';
END $$;
