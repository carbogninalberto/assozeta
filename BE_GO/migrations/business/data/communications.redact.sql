WITH entries AS (
    SELECT entry.id, CASE
        WHEN jsonb_typeof(entry.changes) = 'object' THEN entry.changes
        WHEN jsonb_typeof(entry.changes) = 'string' AND pg_input_is_valid(entry.changes #>> '{}', 'jsonb')
            THEN (entry.changes #>> '{}')::jsonb
    END AS changes
    FROM auditlog_logentry entry
    JOIN django_content_type c ON c.id = entry.content_type_id
    WHERE (c.app_label, c.model) = ('communications', 'communicationconfiguration')
)
UPDATE auditlog_logentry entry
SET changes = entries.changes - 'email_smtp_password'
FROM entries
WHERE entry.id = entries.id AND jsonb_typeof(entries.changes) = 'object'
    AND entries.changes ? 'email_smtp_password';
