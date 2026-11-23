-- JSONField was introduced alongside the legacy social-auth JSON columns.
UPDATE social_auth_usersocialauth
SET extra_data_new = CASE
    WHEN jsonb_typeof(extra_data) = 'string' AND pg_input_is_valid(extra_data #>> '{}', 'jsonb')
        THEN (extra_data #>> '{}')::jsonb
    ELSE extra_data
END
-- Django excludes the string containing two quote characters.
WHERE extra_data <> to_jsonb('""'::text);

UPDATE social_auth_partial
SET data_new = CASE
    WHEN jsonb_typeof(data) = 'string' AND pg_input_is_valid(data #>> '{}', 'jsonb')
        THEN (data #>> '{}')::jsonb
    ELSE data
END;
