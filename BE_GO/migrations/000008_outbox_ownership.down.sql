DO $$ BEGIN
    RAISE EXCEPTION 'Outbox effects and relay delivery history must survive binary rollback';
END $$;
