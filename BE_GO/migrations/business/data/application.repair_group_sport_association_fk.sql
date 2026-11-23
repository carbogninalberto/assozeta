DO $$
DECLARE
    existing record;
    has_expected boolean := false;
BEGIN
    FOR existing IN
        SELECT c.conname, c.confrelid, c.confkey, target.attnum AS target_column
        FROM pg_constraint c
        JOIN pg_attribute source ON source.attrelid = c.conrelid
            AND source.attname = 'sport_association_id' AND c.conkey = ARRAY[source.attnum]
        JOIN pg_attribute target ON target.attrelid = 'application_sportassociation'::regclass
            AND target.attname = 'sport_association_id'
        WHERE c.conrelid = 'application_group'::regclass AND c.contype = 'f'
    LOOP
        IF existing.confrelid = 'application_sportassociation'::regclass
            AND existing.confkey = ARRAY[existing.target_column] THEN
            has_expected := true;
        ELSE
            EXECUTE format('ALTER TABLE application_group DROP CONSTRAINT %I', existing.conname);
        END IF;
    END LOOP;

    IF NOT has_expected THEN
        ALTER TABLE application_group
            ADD CONSTRAINT application_group_sport_association_id_fk_sportassociation
            FOREIGN KEY (sport_association_id) REFERENCES application_sportassociation (sport_association_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;
